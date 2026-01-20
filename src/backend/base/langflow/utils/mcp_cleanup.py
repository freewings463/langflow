"""
模块名称：mcp_cleanup

本模块提供MCP子进程清理实用工具，主要用于优雅关闭。
主要功能包括：
- 在Langflow关闭期间正确终止由stdio_client生成的MCP服务器子进程
- 提供备用机制强制终止MCP进程

设计背景：在应用关闭时需要确保所有MCP会话都被正确清理，防止僵尸进程
注意事项：仅在macOS和Linux上工作
"""

from __future__ import annotations

import contextlib
import sys
from typing import TYPE_CHECKING

from lfx.log.logger import logger

if TYPE_CHECKING:
    import psutil as psutil_type


async def cleanup_mcp_sessions() -> None:
    """清理所有MCP会话以确保子进程被正确终止。
    
    关键路径（三步）：
    1) 尝试从缓存中获取MCP会话管理器并清理所有会话
    2) 作为备用方案，终止任何仍在运行的MCP进程
    3) 记录清理操作的结果
    
    异常流：所有异常都被抑制，确保关闭流程继续
    性能瓶颈：进程终止操作
    排障入口：检查是否有MCP子进程在应用关闭后仍然运行
    """
    with contextlib.suppress(Exception):
        from lfx.base.mcp.util import MCPSessionManager
        from lfx.services.cache.utils import CACHE_MISS

        from langflow.services.deps import get_shared_component_cache_service

        cache_service = get_shared_component_cache_service()
        session_manager = cache_service.get("mcp_session_manager")

        if session_manager is not CACHE_MISS and isinstance(session_manager, MCPSessionManager):
            await session_manager.cleanup_all()

    # Fallback: Kill any MCP server processes (Unix only)
    with contextlib.suppress(Exception):
        await _kill_mcp_processes()


async def _kill_mcp_processes() -> None:
    """终止由此Langflow进程生成的MCP服务器子进程。
    
    关键路径（三步）：
    1) 检查平台是否为Windows，如果是则直接返回
    2) 导入psutil库并终止子MCP进程
    3) 终止孤儿MCP进程并记录结果
    
    异常流：如果psutil无法导入或出现异常，则静默返回
    性能瓶颈：进程查找和终止操作
    排障入口：检查是否仍有MCP进程在运行
    """
    if sys.platform == "win32":
        return

    try:
        import psutil
    except ImportError:
        return

    with contextlib.suppress(Exception):
        killed_count = await _terminate_child_mcp_processes(psutil)
        killed_count += await _terminate_orphaned_mcp_processes(psutil)

        if killed_count > 0:
            await logger.ainfo(f"Killed {killed_count} MCP processes")


async def _terminate_child_mcp_processes(psutil: psutil_type) -> int:
    """终止此进程的子MCP进程。
    
    关键路径（三步）：
    1) 获取当前进程及其所有子进程
    2) 遍历子进程并尝试终止MCP进程
    3) 统计终止的进程数量
    
    异常流：如果当前进程不存在，则返回0
    性能瓶颈：遍历所有子进程
    排障入口：检查返回的计数是否符合预期
    """
    killed_count = 0

    try:
        current_process = psutil.Process()
        children = current_process.children(recursive=True)
    except psutil.NoSuchProcess:
        return 0

    for proc in children:
        if await _try_terminate_mcp_process(proc, psutil):
            killed_count += 1

    return killed_count


async def _terminate_orphaned_mcp_processes(psutil: psutil_type) -> int:
    """在Unix系统上终止孤儿MCP进程(ppid=1)。
    
    关键路径（三步）：
    1) 遍历系统中的所有进程
    2) 过滤出父进程ID为1的进程（孤儿进程）
    3) 尝试终止符合条件的MCP进程
    
    异常流：跳过无法访问或不存在的进程
    性能瓶颈：遍历系统中所有进程
    排障入口：检查返回的计数是否符合预期
    """
    killed_count = 0

    for proc in psutil.process_iter(["pid", "ppid", "cmdline"]):
        try:
            info = proc.info
            if info.get("ppid", 0) != 1:
                continue

            if await _try_terminate_mcp_process(proc, psutil):
                killed_count += 1

        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            continue

    return killed_count


async def _try_terminate_mcp_process(proc: psutil_type.Process, psutil: psutil_type) -> bool:
    """尝试终止进程（如果是MCP服务器进程）。
    
    关键路径（三步）：
    1) 检查进程命令行是否包含'mcp-server'或'mcp-proxy'
    2) 发送终止信号并等待进程结束
    3) 如果超时则强制杀死进程
    
    异常流：无法访问或不存在的进程返回False
    性能瓶颈：进程终止和等待操作
    排障入口：检查进程是否被成功终止
    """
    try:
        cmdline = proc.cmdline()
        cmdline_str = " ".join(cmdline) if cmdline else ""

        if "mcp-server" not in cmdline_str and "mcp-proxy" not in cmdline_str:
            return False

        proc.terminate()
        try:
            proc.wait(timeout=2)
        except psutil.TimeoutExpired:
            proc.kill()

    except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
        return False
    else:
        return True
