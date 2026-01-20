"""
模块名称：MCP Composer 服务

模块目的：负责 MCP Composer 进程的启动、停止与端口管理。
使用场景：为每个项目启动独立 Composer 实例并提供鉴权/代理能力。
主要功能包括：
- 端口检查与冲突处理
- 子进程启动与输出解析
- 配置校验与错误提示

关键组件：
- `MCPComposerService`：服务主类
- 异常体系：`MCPComposerError` 及其子类

设计背景：需要在多项目环境中稳定编排 MCP 服务器并提供统一可观测性。
注意：包含跨平台进程管理逻辑，修改需关注 Windows/Unix 差异。
"""

import asyncio
import json
import os
import platform
import re
import select
import socket
import subprocess
import tempfile
import typing
from collections.abc import Callable
from functools import wraps
from pathlib import Path
from typing import Any

from lfx.log.logger import logger
from lfx.services.base import Service
from lfx.services.deps import get_settings_service

GENERIC_STARTUP_ERROR_MSG = (
    "MCP Composer startup failed. Check OAuth configuration and check logs for more information."
)


class MCPComposerError(Exception):
    """MCP Composer 基类异常。"""

    def __init__(self, message: str | None, project_id: str | None = None):
        if not message:
            message = GENERIC_STARTUP_ERROR_MSG
        self.message = message
        self.project_id = project_id
        super().__init__(message)


class MCPComposerPortError(MCPComposerError):
    """端口被占用或不可用。"""


class MCPComposerConfigError(MCPComposerError):
    """配置不合法。"""


class MCPComposerDisabledError(MCPComposerError):
    """配置中禁用了 MCP Composer。"""


class MCPComposerStartupError(MCPComposerError):
    """启动 MCP Composer 进程失败。"""


def require_composer_enabled(func: Callable) -> Callable:
    """装饰器：在调用前校验 MCP Composer 是否启用。"""

    @wraps(func)
    def wrapper(self, *args, **kwargs):
        if not get_settings_service().settings.mcp_composer_enabled:
            project_id = kwargs.get("project_id")
            error_msg = "MCP Composer is disabled in settings"
            raise MCPComposerDisabledError(error_msg, project_id)

        return func(self, *args, **kwargs)

    return wrapper


class MCPComposerService(Service):
    """按项目管理 MCP Composer 实例的服务。"""

    name = "mcp_composer_service"

    def __init__(self):
        super().__init__()
        self.project_composers: dict[str, dict] = {}
        self._start_locks: dict[str, asyncio.Lock] = {}
        self._active_start_tasks: dict[str, asyncio.Task] = {}
        self._port_to_project: dict[int, str] = {}
        self._pid_to_project: dict[int, str] = {}
        self._last_errors: dict[str, str] = {}

    def get_last_error(self, project_id: str) -> str | None:
        """获取项目的最近错误信息（若存在）。"""
        return self._last_errors.get(project_id)

    def set_last_error(self, project_id: str, error_message: str) -> None:
        """记录项目的最近错误信息。"""
        self._last_errors[project_id] = error_message

    def clear_last_error(self, project_id: str) -> None:
        """清除项目的最近错误信息。"""
        self._last_errors.pop(project_id, None)

    def _is_port_available(self, port: int, host: str = "localhost") -> bool:
        """通过绑定检查端口是否可用。

        契约：返回 True 表示端口空闲；端口非法抛 `ValueError`。
        注意：同时校验 IPv4/IPv6，避免“半绑定”误判。
        """
        import errno

        max_port = 65535
        if not isinstance(port, int) or port < 0 or port > max_port:
            msg = f"Invalid port number: {port}. Port must be between 0 and {max_port}."
            raise ValueError(msg)

        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
                sock.bind((host, port))
        except OSError:
            return False

        try:
            with socket.socket(socket.AF_INET6, socket.SOCK_STREAM) as sock:
                ipv6_host = "::1" if host in ("localhost", "127.0.0.1") else host
                sock.bind((ipv6_host, port))
        except OSError as e:
            if e.errno in (errno.EADDRINUSE, 10048):
                return False

        return True

    async def _kill_process_on_port(self, port: int) -> bool:
        """结束占用指定端口的进程（跨平台）。

        关键路径（三步）：
        1) 判定平台并选择 PID 发现方式
        2) 解析 PID 列表并尝试终止
        3) 记录日志并返回是否成功

        异常流：系统命令失败时返回 False 并记录日志。
        性能：依赖系统命令执行耗时。
        排障：检查端口占用与系统权限。
        """
        try:
            await logger.adebug(f"Checking for processes using port {port}...")
            os_type = platform.system()

            if os_type == "Windows":
                netstat_cmd = os.path.join(os.environ.get("SYSTEMROOT", "C:\\Windows"), "System32", "netstat.exe")  # noqa: PTH118
                result = await asyncio.to_thread(
                    subprocess.run,
                    [netstat_cmd, "-ano"],
                    capture_output=True,
                    text=True,
                    check=False,
                )

                if result.returncode == 0:
                    windows_pids: list[int] = []
                    for line in result.stdout.split("\n"):
                        if f":{port}" in line and "LISTENING" in line:
                            parts = line.split()
                            if parts:
                                try:
                                    pid = int(parts[-1])
                                    windows_pids.append(pid)
                                except (ValueError, IndexError):
                                    continue

                    await logger.adebug(f"Found {len(windows_pids)} process(es) using port {port}: {windows_pids}")

                    for pid in windows_pids:
                        try:
                            await logger.adebug(f"Attempting to kill process {pid} on port {port}...")
                            taskkill_cmd = os.path.join(  # noqa: PTH118
                                os.environ.get("SYSTEMROOT", "C:\\Windows"), "System32", "taskkill.exe"
                            )
                            kill_result = await asyncio.to_thread(
                                subprocess.run,
                                [taskkill_cmd, "/F", "/PID", str(pid)],
                                capture_output=True,
                                check=False,
                            )

                            if kill_result.returncode == 0:
                                await logger.adebug(f"Successfully killed process {pid} on port {port}")
                                return True
                            await logger.awarning(
                                f"taskkill returned {kill_result.returncode} for process {pid} on port {port}"
                            )
                        except Exception as e:  # noqa: BLE001
                            await logger.aerror(f"Error killing PID {pid}: {e}")

                    return False
            else:
                result = await asyncio.to_thread(
                    subprocess.run,
                    ["lsof", "-ti", f":{port}"],
                    capture_output=True,
                    text=True,
                    check=False,
                )

                await logger.adebug(f"lsof returned code {result.returncode} for port {port}")

                lsof_output = result.stdout.strip()
                lsof_errors = result.stderr.strip()

                if lsof_output:
                    await logger.adebug(f"lsof stdout: {lsof_output}")
                if lsof_errors:
                    await logger.adebug(f"lsof stderr: {lsof_errors}")

                if result.returncode == 0 and lsof_output:
                    unix_pids = lsof_output.split("\n")
                    await logger.adebug(f"Found {len(unix_pids)} process(es) using port {port}: {unix_pids}")

                    for pid_str in unix_pids:
                        try:
                            pid = int(pid_str.strip())
                            await logger.adebug(f"Attempting to kill process {pid} on port {port}...")

                            kill_result = await asyncio.to_thread(
                                subprocess.run,
                                ["kill", "-9", str(pid)],
                                capture_output=True,
                                check=False,
                            )

                            if kill_result.returncode == 0:
                                await logger.adebug(f"Successfully sent kill signal to process {pid} on port {port}")
                                return True
                            await logger.awarning(
                                f"kill command returned {kill_result.returncode} for process {pid} on port {port}"
                            )
                        except (ValueError, ProcessLookupError) as e:
                            await logger.aerror(f"Error processing PID {pid_str}: {e}")

                    return False
                await logger.adebug(f"No process found using port {port}")
                return False
        except Exception as e:  # noqa: BLE001
            await logger.aerror(f"Error finding/killing process on port {port}: {e}")
            return False
        return False

    async def _kill_zombie_mcp_processes(self, port: int) -> bool:
        """清理可能僵死的 MCP Composer 进程（Windows 侧）。

        关键路径（三步）：
        1) 基于端口扫描并清理未跟踪 PID
        2) 通过 PowerShell 识别孤儿进程
        3) 适当等待端口释放并返回结果

        异常流：异常会被吞并并返回 False。
        性能：依赖系统命令执行耗时。
        排障：检查 PowerShell/netstat 可用性。
        """
        try:
            os_type = platform.system()
            if os_type != "Windows":
                return False

            await logger.adebug(f"Looking for zombie MCP Composer processes on Windows for port {port}...")

            netstat_cmd = os.path.join(os.environ.get("SYSTEMROOT", "C:\\Windows"), "System32", "netstat.exe")  # noqa: PTH118
            netstat_result = await asyncio.to_thread(
                subprocess.run,
                [netstat_cmd, "-ano"],
                capture_output=True,
                text=True,
                check=False,
            )

            killed_any = False
            if netstat_result.returncode == 0:
                pids_on_port: list[int] = []
                for line in netstat_result.stdout.split("\n"):
                    if f":{port}" in line and "LISTENING" in line:
                        parts = line.split()
                        if parts:
                            try:
                                pid = int(parts[-1])
                                if pid not in self._pid_to_project:
                                    pids_on_port.append(pid)
                                else:
                                    project = self._pid_to_project[pid]
                                    await logger.adebug(
                                        f"Process {pid} on port {port} is tracked, skipping (project: {project})"
                                    )
                            except (ValueError, IndexError):
                                continue

                if pids_on_port:
                    await logger.adebug(
                        f"Found {len(pids_on_port)} untracked process(es) on port {port}: {pids_on_port}"
                    )
                    for pid in pids_on_port:
                        try:
                            await logger.adebug(f"Killing process {pid} on port {port}...")
                            taskkill_cmd = os.path.join(  # noqa: PTH118
                                os.environ.get("SYSTEMROOT", "C:\\Windows"), "System32", "taskkill.exe"
                            )
                            kill_result = await asyncio.to_thread(
                                subprocess.run,
                                [taskkill_cmd, "/F", "/PID", str(pid)],
                                capture_output=True,
                                check=False,
                            )
                            if kill_result.returncode == 0:
                                await logger.adebug(f"Successfully killed process {pid} on port {port}")
                                killed_any = True
                            else:
                                stderr_output = (
                                    kill_result.stderr.decode()
                                    if isinstance(kill_result.stderr, bytes)
                                    else kill_result.stderr
                                )
                                await logger.awarning(f"Failed to kill process {pid} on port {port}: {stderr_output}")
                        except Exception as e:  # noqa: BLE001
                            await logger.adebug(f"Error killing process {pid}: {e}")

            try:
                ps_filter = (
                    f"$_.Name -eq 'python.exe' -and $_.CommandLine -like '*mcp-composer*' "
                    f"-and ($_.CommandLine -like '*--port {port}*' -or $_.CommandLine -like '*--port={port}*')"
                )
                ps_cmd = (
                    f"Get-WmiObject Win32_Process | Where-Object {{ {ps_filter} }} | "
                    "Select-Object ProcessId,CommandLine | ConvertTo-Json"
                )
                powershell_cmd = ["powershell.exe", "-NoProfile", "-Command", ps_cmd]

                ps_result = await asyncio.to_thread(
                    subprocess.run,
                    powershell_cmd,
                    capture_output=True,
                    text=True,
                    check=False,
                    timeout=5,
                )

                if ps_result.returncode == 0 and ps_result.stdout.strip():
                    try:
                        processes = json.loads(ps_result.stdout)
                        if isinstance(processes, dict):
                            processes = [processes]
                        elif not isinstance(processes, list):
                            processes = []

                        for proc in processes:
                            try:
                                pid = int(proc.get("ProcessId", 0))
                                if pid <= 0 or pid in self._pid_to_project:
                                    continue

                                await logger.adebug(
                                    f"Found orphaned MCP Composer process {pid} for port {port}, killing it"
                                )
                                taskkill_cmd = os.path.join(  # noqa: PTH118
                                    os.environ.get("SYSTEMROOT", "C:\\Windows"), "System32", "taskkill.exe"
                                )
                                kill_result = await asyncio.to_thread(
                                    subprocess.run,
                                    [taskkill_cmd, "/F", "/PID", str(pid)],
                                    capture_output=True,
                                    check=False,
                                )
                                if kill_result.returncode == 0:
                                    await logger.adebug(f"Successfully killed orphaned process {pid}")
                                    killed_any = True

                            except (ValueError, KeyError) as e:
                                await logger.adebug(f"Error processing PowerShell result: {e}")
                                continue

                    except json.JSONDecodeError as e:
                        await logger.adebug(f"Failed to parse PowerShell output: {e}")

            except asyncio.TimeoutError:
                await logger.adebug("PowerShell command timed out while checking for orphaned processes")
            except Exception as e:  # noqa: BLE001
                await logger.adebug(f"Error using PowerShell to find orphaned processes: {e}")

            if killed_any:
                await logger.adebug("Waiting 3 seconds for Windows to release port...")
                await asyncio.sleep(3)

            return killed_any  # noqa: TRY300

        except Exception as e:  # noqa: BLE001
            await logger.adebug(f"Error killing zombie processes: {e}")
            return False

    def _is_port_used_by_another_project(self, port: int, current_project_id: str) -> tuple[bool, str | None]:
        """判断端口是否被其他项目占用。"""
        other_project_id = self._port_to_project.get(port)
        if other_project_id and other_project_id != current_project_id:
            return True, other_project_id
        return False, None

    async def start(self):
        """启动服务（仅输出启用状态日志）。"""
        settings = get_settings_service().settings
        if not settings.mcp_composer_enabled:
            await logger.adebug(
                "MCP Composer is disabled in settings. OAuth authentication will not be enabled for MCP Servers."
            )
        else:
            await logger.adebug(
                "MCP Composer is enabled in settings. OAuth authentication will be enabled for MCP Servers."
            )

    async def stop(self):
        """停止所有项目的 MCP Composer 实例。"""
        for project_id in list(self.project_composers.keys()):
            await self.stop_project_composer(project_id)
        await logger.adebug("All MCP Composer instances stopped")

    @require_composer_enabled
    async def stop_project_composer(self, project_id: str):
        """停止指定项目的 MCP Composer 实例。"""
        if project_id not in self.project_composers:
            return

        if project_id in self._start_locks:
            async with self._start_locks[project_id]:
                await self._do_stop_project_composer(project_id)
                del self._start_locks[project_id]
        else:
            await self._do_stop_project_composer(project_id)

    async def _do_stop_project_composer(self, project_id: str):
        """内部停止逻辑（释放进程与跟踪信息）。"""
        if project_id not in self.project_composers:
            return

        composer_info = self.project_composers[project_id]
        process = composer_info.get("process")

        try:
            if process:
                try:
                    if process.poll() is None:
                        await logger.adebug(f"Terminating MCP Composer process {process.pid} for project {project_id}")
                        process.terminate()

                        try:
                            await asyncio.wait_for(asyncio.to_thread(process.wait), timeout=2.0)
                            await logger.adebug(f"MCP Composer for project {project_id} terminated gracefully")
                        except asyncio.TimeoutError:
                            await logger.aerror(
                                f"MCP Composer for project {project_id} did not terminate gracefully, force killing"
                            )
                            await asyncio.to_thread(process.kill)
                            await asyncio.to_thread(process.wait)
                    else:
                        await logger.adebug(f"MCP Composer process for project {project_id} was already terminated")

                    await logger.adebug(f"MCP Composer stopped for project {project_id}")

                except ProcessLookupError:
                    await logger.adebug(f"MCP Composer process for project {project_id} was already terminated")
                except Exception as e:  # noqa: BLE001
                    await logger.aerror(f"Error stopping MCP Composer for project {project_id}: {e}")
        finally:
            port = composer_info.get("port")
            if port and self._port_to_project.get(port) == project_id:
                self._port_to_project.pop(port, None)
                await logger.adebug(f"Released port {port} from project {project_id}")

            if process and process.pid:
                self._pid_to_project.pop(process.pid, None)
                await logger.adebug(f"Released PID {process.pid} tracking for project {project_id}")

            self.project_composers.pop(project_id, None)
            await logger.adebug(f"Removed tracking for project {project_id}")

    async def _wait_for_process_exit(self, process):
        """等待子进程退出。"""
        await asyncio.to_thread(process.wait)

    async def _read_process_output_and_extract_error(
        self,
        process: subprocess.Popen,
        oauth_server_url: str | None,
        timeout: float = 2.0,
        stdout_file=None,
        stderr_file=None,
    ) -> tuple[str, str, str]:
        """读取进程输出并提取友好错误信息。

        关键路径（三步）：
        1) 读取 stdout/stderr（Windows 可能走临时文件）
        2) 清理与解码输出
        3) 抽取可读错误信息

        异常流：超时会终止进程并返回通用错误。
        性能：受进程输出量影响。
        排障：检查输出是否为空与编码错误。
        """
        stdout_content = ""
        stderr_content = ""

        try:
            if stdout_file and stderr_file:
                try:
                    stdout_file.close()
                    stderr_file.close()
                except Exception as e:  # noqa: BLE001
                    await logger.adebug(f"Error closing temp files: {e}")

                try:

                    def read_file(filepath):
                        return Path(filepath).read_bytes()

                    stdout_bytes = await asyncio.to_thread(read_file, stdout_file.name)
                    stdout_content = stdout_bytes.decode("utf-8", errors="replace") if stdout_bytes else ""
                except Exception as e:  # noqa: BLE001
                    await logger.adebug(f"Error reading stdout file: {e}")

                try:

                    def read_file(filepath):
                        return Path(filepath).read_bytes()

                    stderr_bytes = await asyncio.to_thread(read_file, stderr_file.name)
                    stderr_content = stderr_bytes.decode("utf-8", errors="replace") if stderr_bytes else ""
                except Exception as e:  # noqa: BLE001
                    await logger.adebug(f"Error reading stderr file: {e}")

                try:
                    Path(stdout_file.name).unlink()
                    Path(stderr_file.name).unlink()
                except Exception as e:  # noqa: BLE001
                    await logger.adebug(f"Error removing temp files: {e}")
            else:
                stdout_bytes, stderr_bytes = await asyncio.to_thread(process.communicate, timeout=timeout)
                stdout_content = stdout_bytes.decode("utf-8", errors="replace") if stdout_bytes else ""
                stderr_content = stderr_bytes.decode("utf-8", errors="replace") if stderr_bytes else ""

        except subprocess.TimeoutExpired:
            process.kill()
            error_msg = self._extract_error_message("", "", oauth_server_url)
            return "", "", error_msg

        error_msg = self._extract_error_message(stdout_content, stderr_content, oauth_server_url)
        return stdout_content, stderr_content, error_msg

    async def _read_stream_non_blocking(self, stream, stream_name: str) -> str:
        """非阻塞读取流内容并输出日志。

        关键路径（三步）：
        1) 判断平台与可用读取方式
        2) 尝试读取一行输出
        3) 记录日志并返回文本
        """
        if not stream:
            return ""

        try:
            os_type = platform.system()

            if os_type == "Windows":
                return ""
            if select.select([stream], [], [], 0)[0]:
                line_bytes = stream.readline()
                if line_bytes:
                    line = line_bytes.decode("utf-8", errors="replace") if isinstance(line_bytes, bytes) else line_bytes
                    stripped = line.strip()
                    if stripped:
                        if stream_name == "stderr" and ("ERROR" in stripped or "error" in stripped):
                            await logger.aerror(f"MCP Composer {stream_name}: {stripped}")
                        else:
                            await logger.adebug(f"MCP Composer {stream_name}: {stripped}")
                        return stripped
        except Exception as e:  # noqa: BLE001
            await logger.adebug(f"Error reading {stream_name}: {e}")
        return ""

    async def _ensure_port_available(self, port: int, current_project_id: str) -> None:
        """确保端口可用，仅清理未跟踪的进程。

        关键路径（三步）：
        1) 校验端口合法性
        2) 判断占用归属（他项目/本项目/外部）
        3) 选择回收或报错

        异常流：端口非法抛 `MCPComposerConfigError`；占用冲突抛 `MCPComposerPortError`。
        排障：检查端口占用进程与项目映射。
        """
        try:
            is_port_available = self._is_port_available(port)
            await logger.adebug(f"Port {port} availability check: {is_port_available}")
        except (ValueError, OverflowError, TypeError) as e:
            error_msg = f"Invalid port number: {port}. Port must be an integer between 0 and 65535."
            await logger.aerror(f"Invalid port for project {current_project_id}: {e}")
            raise MCPComposerConfigError(error_msg, current_project_id) from e

        if not is_port_available:
            is_used_by_other, other_project_id = self._is_port_used_by_another_project(port, current_project_id)

            if is_used_by_other and other_project_id:
                other_composer = self.project_composers.get(other_project_id)
                if other_composer and other_composer.get("process"):
                    other_process = other_composer["process"]
                    if other_process.poll() is None:
                        await logger.aerror(
                            f"Port {port} requested by project {current_project_id} is already in use by "
                            f"project {other_project_id}. Will not kill active MCP Composer process."
                        )
                        port_error_msg = (
                            f"Port {port} is already in use by another project. "
                            f"Please choose a different port (e.g., {port + 1}) "
                            f"or disable OAuth on the other project first."
                        )
                        raise MCPComposerPortError(port_error_msg, current_project_id)

                    await logger.adebug(
                        f"Port {port} was tracked to project {other_project_id} but process died. "
                        f"Allowing project {current_project_id} to take ownership."
                    )
                    await self._do_stop_project_composer(other_project_id)

            port_owner_project = self._port_to_project.get(port)
            if port_owner_project == current_project_id:
                await logger.adebug(
                    f"Port {port} is in use by current project {current_project_id} (likely stuck in startup). "
                    f"Killing process to retry."
                )
                killed = await self._kill_process_on_port(port)
                if killed:
                    await logger.adebug(
                        f"Successfully killed own process on port {port}. Waiting for port to be released..."
                    )
                    await asyncio.sleep(2)
                    is_port_available = self._is_port_available(port)
                    if not is_port_available:
                        await logger.aerror(f"Port {port} is still in use after killing own process.")
                        port_error_msg = f"Port {port} is still in use after killing process"
                        raise MCPComposerPortError(port_error_msg)
            else:
                await logger.aerror(
                    f"Port {port} is in use by an unknown process (not owned by Langflow). "
                    f"Will not kill external application for security reasons."
                )
                port_error_msg = (
                    f"Port {port} is already in use by another application. "
                    f"Please choose a different port (e.g., {port + 1}) or free up the port manually."
                )
                raise MCPComposerPortError(port_error_msg, current_project_id)

        await logger.adebug(f"Port {port} is available, proceeding with MCP Composer startup")

    async def _log_startup_error_details(
        self,
        project_id: str,
        cmd: list[str],
        host: str,
        port: int,
        stdout: str = "",
        stderr: str = "",
        error_msg: str = "",
        exit_code: int | None = None,
        pid: int | None = None,
    ) -> None:
        """记录启动失败的详细信息。

        关键路径（三步）：
        1) 输出基础上下文（项目/端口/命令）
        2) 输出 stdout/stderr 与错误摘要
        3) 供 UI/日志检索使用
        """
        await logger.aerror(f"MCP Composer startup failed for project {project_id}:")
        if exit_code is not None:
            await logger.aerror(f"  - Process died with exit code: {exit_code}")
        if pid is not None:
            await logger.aerror(f"  - Process is running (PID: {pid}) but failed to bind to port {port}")
        await logger.aerror(f"  - Target: {host}:{port}")

        safe_cmd = self._obfuscate_command_secrets(cmd)
        await logger.aerror(f"  - Command: {' '.join(safe_cmd)}")

        if stderr.strip():
            await logger.aerror(f"  - Error output: {stderr.strip()}")
        if stdout.strip():
            await logger.aerror(f"  - Standard output: {stdout.strip()}")
        if error_msg:
            await logger.aerror(f"  - Error message: {error_msg}")

    def _validate_oauth_settings(self, auth_config: dict[str, Any]) -> None:
        """校验 OAuth 必填字段是否完整。

        异常流：缺失或为空时抛 `MCPComposerConfigError`。
        """
        if auth_config.get("auth_type") != "oauth":
            return

        required_fields = [
            "oauth_host",
            "oauth_port",
            "oauth_server_url",
            "oauth_auth_url",
            "oauth_token_url",
            "oauth_client_id",
            "oauth_client_secret",
        ]

        missing_fields = []
        empty_fields = []

        for field in required_fields:
            value = auth_config.get(field)
            if value is None:
                missing_fields.append(field)
            elif not str(value).strip():
                empty_fields.append(field)

        error_parts = []
        if missing_fields:
            error_parts.append(f"Missing required fields: {', '.join(missing_fields)}")
        if empty_fields:
            error_parts.append(f"Empty required fields: {', '.join(empty_fields)}")

        if error_parts:
            config_error_msg = f"Invalid OAuth configuration: {'; '.join(error_parts)}"
            raise MCPComposerConfigError(config_error_msg)

    @staticmethod
    def _normalize_config_value(value: Any) -> Any:
        """归一化配置值（空字符串与 None 统一为 None）。"""
        return None if (value is None or value == "") else value

    def _has_auth_config_changed(self, existing_auth: dict[str, Any] | None, new_auth: dict[str, Any] | None) -> bool:
        """判断鉴权配置是否变化以触发重启。"""
        if not existing_auth and not new_auth:
            return False

        if not existing_auth or not new_auth:
            return True

        auth_type = new_auth.get("auth_type", "")

        if existing_auth.get("auth_type") != auth_type:
            return True

        fields_to_check = []
        if auth_type == "oauth":
            all_keys = set(existing_auth.keys()) | set(new_auth.keys())
            fields_to_check = [k for k in all_keys if k.startswith("oauth_") or k in ["host", "port"]]
        elif auth_type == "apikey":
            fields_to_check = ["api_key"]

        for field in fields_to_check:
            old_normalized = self._normalize_config_value(existing_auth.get(field))
            new_normalized = self._normalize_config_value(new_auth.get(field))

            if old_normalized != new_normalized:
                return True

        return False

    def _obfuscate_command_secrets(self, cmd: list[str]) -> list[str]:
        """对命令行参数中的敏感值进行脱敏。"""
        safe_cmd = []
        i = 0

        while i < len(cmd):
            arg = cmd[i]

            if arg == "--env" and i + 2 < len(cmd):
                env_key = cmd[i + 1]
                env_value = cmd[i + 2]

                if any(secret in env_key.lower() for secret in ["secret", "key", "token"]):
                    safe_cmd.extend([arg, env_key, "***REDACTED***"])
                    i += 3
                    continue

                safe_cmd.extend([arg, env_key, env_value])
                i += 3
                continue

            safe_cmd.append(arg)
            i += 1

        return safe_cmd

    def _extract_error_message(
        self, stdout_content: str, stderr_content: str, oauth_server_url: str | None = None
    ) -> str:
        """从子进程输出中提取友好的错误消息。

        关键路径（三步）：
        1) 合并 stdout/stderr
        2) 按模式匹配常见错误
        3) 回退到通用错误
        """
        combined_output = (stderr_content + "\n" + stdout_content).strip()
        if not oauth_server_url:
            oauth_server_url = "OAuth server URL"

        error_patterns = [
            (r"address already in use", f"Address {oauth_server_url} is already in use."),
            (r"permission denied", f"Permission denied starting MCP Composer on address {oauth_server_url}."),
            (
                r"connection refused",
                f"Connection refused on address {oauth_server_url}. The address may be blocked or unavailable.",
            ),
            (
                r"bind.*failed",
                f"Failed to bind to address {oauth_server_url}. The address may be in use or unavailable.",
            ),
            (r"timeout", "MCP Composer startup timed out. Please try again."),
            (r"invalid.*configuration", "Invalid MCP Composer configuration. Please check your settings."),
            (r"oauth.*error", "OAuth configuration error. Please check your OAuth settings."),
            (r"authentication.*failed", "Authentication failed. Please check your credentials."),
        ]

        for pattern, friendly_msg in error_patterns:
            if re.search(pattern, combined_output, re.IGNORECASE):
                return friendly_msg

        return GENERIC_STARTUP_ERROR_MSG

    @require_composer_enabled
    async def start_project_composer(
        self,
        project_id: str,
        streamable_http_url: str,
        auth_config: dict[str, Any] | None,
        max_retries: int = 3,
        max_startup_checks: int = 40,
        startup_delay: float = 2.0,
        *,
        legacy_sse_url: str | None = None,
    ) -> None:
        """启动指定项目的 MCP Composer 实例。

        关键路径（三步）：
        1) 取消旧的启动任务
        2) 记录当前任务并进入启动流程
        3) 清理任务引用
        """
        # 取消该项目已有的启动任务
        if project_id in self._active_start_tasks:
            active_task = self._active_start_tasks[project_id]
            if not active_task.done():
                await logger.adebug(f"Cancelling previous MCP Composer start operation for project {project_id}")
                active_task.cancel()
                try:
                    await active_task
                except asyncio.CancelledError:
                    await logger.adebug(f"Previous start operation for project {project_id} cancelled successfully")
                finally:
                    del self._active_start_tasks[project_id]

        current_task = asyncio.current_task()
        if not current_task:
            await logger.awarning(
                f"Could not get current task for project {project_id}. "
                f"Concurrent start operations may not be properly cancelled."
            )
        else:
            self._active_start_tasks[project_id] = current_task

        try:
            await self._do_start_project_composer(
                project_id,
                streamable_http_url,
                auth_config,
                max_retries,
                max_startup_checks,
                startup_delay,
                legacy_sse_url=legacy_sse_url,
            )
        finally:
            if project_id in self._active_start_tasks and self._active_start_tasks[project_id] == current_task:
                del self._active_start_tasks[project_id]

    async def _do_start_project_composer(
        self,
        project_id: str,
        streamable_http_url: str,
        auth_config: dict[str, Any] | None,
        max_retries: int = 3,
        max_startup_checks: int = 40,
        startup_delay: float = 2.0,
        *,
        legacy_sse_url: str | None = None,
    ) -> None:
        """内部启动逻辑（带重试与端口校验）。

        关键路径（三步）：
        1) 校验配置并获取锁
        2) 检查端口并按需清理
        3) 启动进程并写入跟踪信息

        异常流：配置/端口问题抛 `MCPComposerConfigError`/`MCPComposerPortError`。
        排障：查看 `self._last_errors` 与启动日志。
        """
        legacy_sse_url = legacy_sse_url or f"{streamable_http_url.rstrip('/')}/sse"
        if not auth_config:
            no_auth_error_msg = "No auth settings provided"
            raise MCPComposerConfigError(no_auth_error_msg, project_id)

        self._validate_oauth_settings(auth_config)

        project_host = auth_config.get("oauth_host") if auth_config else "unknown"
        project_port = auth_config.get("oauth_port") if auth_config else "unknown"
        await logger.adebug(f"Starting MCP Composer for project {project_id} on {project_host}:{project_port}")

        if project_id not in self._start_locks:
            self._start_locks[project_id] = asyncio.Lock()

        async with self._start_locks[project_id]:
            project_port_str = auth_config.get("oauth_port")
            if not project_port_str:
                no_port_error_msg = "No OAuth port provided"
                raise MCPComposerConfigError(no_port_error_msg, project_id)

            try:
                project_port = int(project_port_str)
            except (ValueError, TypeError) as e:
                port_error_msg = f"Invalid OAuth port: {project_port_str}"
                raise MCPComposerConfigError(port_error_msg, project_id) from e

            project_host = auth_config.get("oauth_host")
            if not project_host:
                no_host_error_msg = "No OAuth host provided"
                raise MCPComposerConfigError(no_host_error_msg, project_id)

            if project_id in self.project_composers:
                composer_info = self.project_composers[project_id]
                process = composer_info.get("process")
                existing_auth = composer_info.get("auth_config", {})
                existing_port = composer_info.get("port")

                if process and process.poll() is None:
                    auth_changed = self._has_auth_config_changed(existing_auth, auth_config)

                    if auth_changed:
                        await logger.adebug(f"Config changed for project {project_id}, restarting MCP Composer")
                        await self._do_stop_project_composer(project_id)
                    else:
                        await logger.adebug(
                            f"MCP Composer already running for project {project_id} with current config"
                        )
                        return
                else:
                    await logger.adebug(f"MCP Composer process died for project {project_id}, restarting")
                    await self._do_stop_project_composer(project_id)
                    if existing_port:
                        try:
                            await asyncio.wait_for(self._kill_process_on_port(existing_port), timeout=5.0)
                        except asyncio.TimeoutError:
                            await logger.aerror(f"Timeout while killing process on port {existing_port}")

            last_error = None
            try:
                try:
                    await logger.adebug(
                        f"Checking for zombie MCP Composer processes on port {project_port} before startup..."
                    )
                    zombies_killed = await self._kill_zombie_mcp_processes(project_port)
                    if zombies_killed:
                        await logger.adebug(f"Killed zombie processes, port {project_port} should now be free")
                except Exception as zombie_error:  # noqa: BLE001
                    await logger.awarning(
                        f"Failed to check/kill zombie processes (non-fatal): {zombie_error}. Continuing with startup..."
                    )

                try:
                    await self._ensure_port_available(project_port, project_id)
                except (MCPComposerPortError, MCPComposerConfigError) as e:
                    self._last_errors[project_id] = e.message
                    raise
                for retry_attempt in range(1, max_retries + 1):
                    try:
                        await logger.adebug(
                            f"Starting MCP Composer for project {project_id} (attempt {retry_attempt}/{max_retries})"
                        )

                        if retry_attempt > 1:
                            await logger.adebug(f"Re-checking port {project_port} availability before retry...")
                            await self._ensure_port_available(project_port, project_id)

                        process = await self._start_project_composer_process(
                            project_id,
                            project_host,
                            project_port,
                            streamable_http_url,
                            auth_config,
                            max_startup_checks,
                            startup_delay,
                            legacy_sse_url=legacy_sse_url,
                        )

                    except MCPComposerError as e:
                        last_error = e
                        await logger.aerror(
                            f"MCP Composer startup attempt {retry_attempt}/{max_retries} failed "
                            f"for project {project_id}: {e.message}"
                        )

                        if isinstance(e, (MCPComposerConfigError, MCPComposerPortError)):
                            await logger.aerror(
                                f"Configuration or port error for project {project_id}, not retrying: {e.message}"
                            )
                            raise

                        if project_id in self.project_composers:
                            await self._do_stop_project_composer(project_id)

                        if retry_attempt < max_retries:
                            await logger.adebug(f"Waiting 2 seconds before retry attempt {retry_attempt + 1}...")
                            await asyncio.sleep(2)

                            try:
                                msg = f"Checking for zombie MCP Composer processes on port {project_port}"
                                await logger.adebug(msg)
                                zombies_killed = await self._kill_zombie_mcp_processes(project_port)
                                if zombies_killed:
                                    await logger.adebug(f"Killed zombie processes, port {project_port} should be free")
                            except Exception as retry_zombie_error:  # noqa: BLE001
                                msg = f"Failed to check/kill zombie processes during retry: {retry_zombie_error}"
                                await logger.awarning(msg)

                    else:
                        self.project_composers[project_id] = {
                            "process": process,
                            "host": project_host,
                            "port": project_port,
                            "streamable_http_url": streamable_http_url,
                            "legacy_sse_url": legacy_sse_url,
                            "sse_url": legacy_sse_url,
                            "auth_config": auth_config,
                        }
                        self._port_to_project[project_port] = project_id
                        self._pid_to_project[process.pid] = project_id
                        self.clear_last_error(project_id)

                        await logger.adebug(
                            f"MCP Composer started for project {project_id} on port {project_port} "
                            f"(PID: {process.pid}) after {retry_attempt} attempt(s)"
                        )
                        return

                if last_error:
                    await logger.aerror(
                        f"MCP Composer failed to start for project {project_id} after {max_retries} attempts"
                    )
                    self._last_errors[project_id] = last_error.message
                    raise last_error

            except asyncio.CancelledError:
                await logger.adebug(f"MCP Composer start operation for project {project_id} was cancelled")
                if project_id in self.project_composers:
                    await self._do_stop_project_composer(project_id)
                raise

    async def _start_project_composer_process(
        self,
        project_id: str,
        host: str,
        port: int,
        streamable_http_url: str,
        auth_config: dict[str, Any] | None = None,
        max_startup_checks: int = 40,
        startup_delay: float = 2.0,
        *,
        legacy_sse_url: str | None = None,
    ) -> subprocess.Popen:
        """启动 MCP Composer 子进程并等待就绪。

        关键路径（三步）：
        1) 组装命令与环境变量
        2) 启动子进程并监控端口绑定
        3) 成功返回进程或抛出启动异常

        异常流：启动失败抛 `MCPComposerStartupError`。
        性能：启动检查次数受 `max_startup_checks` 影响。
        """
        settings = get_settings_service().settings
        effective_legacy_sse_url = legacy_sse_url or f"{streamable_http_url.rstrip('/')}/sse"

        cmd = [
            "uvx",
            f"mcp-composer{settings.mcp_composer_version}",
            "--port",
            str(port),
            "--host",
            host,
            "--mode",
            "http",
            "--endpoint",
            streamable_http_url,
            "--sse-url",
            effective_legacy_sse_url,
            "--disable-composer-tools",
        ]

        env = os.environ.copy()

        oauth_server_url = auth_config.get("oauth_server_url") if auth_config else None
        if auth_config:
            auth_type = auth_config.get("auth_type")

            if auth_type == "oauth":
                cmd.extend(["--auth_type", "oauth"])

                cmd.extend(["--env", "ENABLE_OAUTH", "True"])

                oauth_env_mapping = {
                    "oauth_host": "OAUTH_HOST",
                    "oauth_port": "OAUTH_PORT",
                    "oauth_server_url": "OAUTH_SERVER_URL",
                    "oauth_callback_url": "OAUTH_CALLBACK_URL",
                    "oauth_client_id": "OAUTH_CLIENT_ID",
                    "oauth_client_secret": "OAUTH_CLIENT_SECRET",  # pragma: allowlist secret
                    "oauth_auth_url": "OAUTH_AUTH_URL",
                    "oauth_token_url": "OAUTH_TOKEN_URL",
                    "oauth_mcp_scope": "OAUTH_MCP_SCOPE",
                    "oauth_provider_scope": "OAUTH_PROVIDER_SCOPE",
                }

                if ("oauth_callback_url" not in auth_config or not auth_config.get("oauth_callback_url")) and (
                    "oauth_callback_path" in auth_config and auth_config.get("oauth_callback_path")
                ):
                    auth_config["oauth_callback_url"] = auth_config["oauth_callback_path"]

                for config_key, env_key in oauth_env_mapping.items():
                    value = auth_config.get(config_key)
                    if value is not None and str(value).strip():
                        cmd.extend(["--env", env_key, str(value)])

        safe_cmd = self._obfuscate_command_secrets(cmd)
        await logger.adebug(f"Starting MCP Composer with command: {' '.join(safe_cmd)}")

        stdout_handle: int | typing.IO[bytes] = subprocess.PIPE
        stderr_handle: int | typing.IO[bytes] = subprocess.PIPE
        stdout_file = None
        stderr_file = None

        if platform.system() == "Windows":
            stdout_file = tempfile.NamedTemporaryFile(  # noqa: SIM115
                mode="w+b", delete=False, prefix=f"mcp_composer_{project_id}_stdout_", suffix=".log"
            )
            stderr_file = tempfile.NamedTemporaryFile(  # noqa: SIM115
                mode="w+b", delete=False, prefix=f"mcp_composer_{project_id}_stderr_", suffix=".log"
            )
            stdout_handle = stdout_file
            stderr_handle = stderr_file
            stdout_name = stdout_file.name
            stderr_name = stderr_file.name
            await logger.adebug(f"Using temp files for MCP Composer logs: stdout={stdout_name}, stderr={stderr_name}")

        process = subprocess.Popen(cmd, env=env, stdout=stdout_handle, stderr=stderr_handle)  # noqa: ASYNC220, S603

        process_running = False
        port_bound = False

        await logger.adebug(
            f"MCP Composer process started with PID {process.pid}, monitoring startup for project {project_id}..."
        )

        try:
            for check in range(max_startup_checks):
                await asyncio.sleep(startup_delay)

                poll_result = process.poll()

                startup_error_msg = None
                if poll_result is not None:
                    (
                        stdout_content,
                        stderr_content,
                        startup_error_msg,
                    ) = await self._read_process_output_and_extract_error(
                        process, oauth_server_url, stdout_file=stdout_file, stderr_file=stderr_file
                    )
                    await self._log_startup_error_details(
                        project_id, cmd, host, port, stdout_content, stderr_content, startup_error_msg, poll_result
                    )
                    raise MCPComposerStartupError(startup_error_msg, project_id)

                port_bound = not self._is_port_available(port)

                if port_bound:
                    await logger.adebug(
                        f"MCP Composer for project {project_id} bound to port {port} "
                        f"(check {check + 1}/{max_startup_checks})"
                    )
                    process_running = True
                    break
                await logger.adebug(
                    f"MCP Composer for project {project_id} not yet bound to port {port} "
                    f"(check {check + 1}/{max_startup_checks})"
                )

                await self._read_stream_non_blocking(process.stderr, "stderr")
                await self._read_stream_non_blocking(process.stdout, "stdout")

        except asyncio.CancelledError:
            await logger.adebug(
                f"MCP Composer process startup cancelled for project {project_id}, terminating process {process.pid}"
            )
            try:
                process.terminate()
                try:
                    await asyncio.wait_for(asyncio.to_thread(process.wait), timeout=2.0)
                except asyncio.TimeoutError:
                    await logger.adebug(f"Process {process.pid} did not terminate gracefully, force killing")
                    await asyncio.to_thread(process.kill)
                    await asyncio.to_thread(process.wait)
            except Exception as e:  # noqa: BLE001
                await logger.adebug(f"Error terminating process during cancellation: {e}")
            raise

        if not process_running or not port_bound:
            poll_result = process.poll()

            if poll_result is not None:
                stdout_content, stderr_content, startup_error_msg = await self._read_process_output_and_extract_error(
                    process, oauth_server_url, stdout_file=stdout_file, stderr_file=stderr_file
                )
                await self._log_startup_error_details(
                    project_id, cmd, host, port, stdout_content, stderr_content, startup_error_msg, poll_result
                )
                raise MCPComposerStartupError(startup_error_msg, project_id)
            await logger.aerror(
                f"  - Checked {max_startup_checks} times over {max_startup_checks * startup_delay} seconds"
            )

            process.terminate()
            stdout_content, stderr_content, startup_error_msg = await self._read_process_output_and_extract_error(
                process, oauth_server_url, stdout_file=stdout_file, stderr_file=stderr_file
            )
            await self._log_startup_error_details(
                project_id, cmd, host, port, stdout_content, stderr_content, startup_error_msg, pid=process.pid
            )
            raise MCPComposerStartupError(startup_error_msg, project_id)

        if stdout_file and stderr_file:
            try:
                stdout_file.close()
                stderr_file.close()
                Path(stdout_file.name).unlink()
                Path(stderr_file.name).unlink()
            except Exception as e:  # noqa: BLE001
                await logger.adebug(f"Error cleaning up temp files on success: {e}")
        else:
            if process.stdout:
                process.stdout.close()
            if process.stderr:
                process.stderr.close()

        return process

    @require_composer_enabled
    def get_project_composer_port(self, project_id: str) -> int | None:
        """获取指定项目的 Composer 端口。"""
        if project_id not in self.project_composers:
            return None
        return self.project_composers[project_id]["port"]

    @require_composer_enabled
    async def teardown(self) -> None:
        """销毁服务并释放资源。"""
        await logger.adebug("Tearing down MCP Composer service...")
        await self.stop()
        await logger.adebug("MCP Composer service teardown complete")
