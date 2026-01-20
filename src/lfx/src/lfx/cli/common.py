"""
模块名称：CLI 公共工具库

本模块提供 CLI 命令的通用能力，主要用于输入校验、图加载、日志捕获与依赖安装。主要功能包括：
- 校验路径/URL 并加载 flow 或脚本
- 执行图并捕获 stdout/stderr
- 解析 PEP-723 依赖并按需安装
- 处理 GitHub/ZIP 资源的下载与解压

关键组件：
- `load_graph_from_path` / `prepare_graph`：图加载与准备
- `execute_graph_with_capture`：运行并捕获日志
- `extract_script_dependencies` / `ensure_dependencies_installed`：脚本依赖处理

设计背景：CLI 需要在无服务端环境中独立完成加载/校验/执行等流程。
注意事项：部分函数有网络与安装副作用，调用方需在 CLI 场景显式提示用户。
"""

from __future__ import annotations

import ast
import contextlib
import importlib.metadata as importlib_metadata
import io
import os
import re
import socket
import subprocess
import sys
import tempfile
import uuid
import zipfile
from io import StringIO
from pathlib import Path
from shutil import which
from typing import TYPE_CHECKING
from urllib.parse import urlparse

import httpx
import typer

from lfx.cli.script_loader import (
    extract_structured_result,
    find_graph_variable,
    load_graph_from_script,
)
from lfx.load import load_flow_from_json
from lfx.schema.schema import InputValueRequest

if TYPE_CHECKING:
    from types import ModuleType

# 注意：优先使用 Python 3.11+ 的 `tomllib`，否则回退到 `tomli`
_toml_parser: ModuleType | None = None
try:
    import tomllib as _toml_parser
except ModuleNotFoundError:
    with contextlib.suppress(ModuleNotFoundError):
        import tomli as toml_parser

        _toml_parser = toml_parser

MAX_PORT_NUMBER = 65535

# 注意：固定命名空间用于生成稳定的 UUIDv5
_LANGFLOW_NAMESPACE_UUID = uuid.UUID("3c091057-e799-4e32-8ebc-27bc31e1108c")

# 注意：GitHub Token 环境变量名
_GITHUB_TOKEN_ENV = "GITHUB_TOKEN"


def create_verbose_printer(*, verbose: bool):
    """创建仅在 verbose 模式输出的打印函数。

    契约：返回的函数仅在 `verbose=True` 时向 stderr 输出字符串。
    失败语义：无。
    副作用：可能向 stderr 写入文本。
    """

    def verbose_print(message: str) -> None:
        """按 verbose 开关输出诊断信息。

        契约：`verbose=True` 时输出到 stderr，否则静默。
        失败语义：无。
        副作用：写入 stderr。
        """
        if verbose:
            typer.echo(message, file=sys.stderr)

    return verbose_print


def is_port_in_use(port: int, host: str = "localhost") -> bool:
    """检查端口是否已被占用。

    契约：返回 True 表示端口无法绑定，False 表示可用。
    失败语义：仅捕获 `OSError`；其他异常上抛。
    副作用：尝试绑定端口。
    """
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind((host, port))
        except OSError:
            return True
        else:
            return False


def get_free_port(starting_port: int = 8000) -> int:
    """从指定起点寻找可用端口。

    契约：从 `starting_port` 向上搜索，返回首个可绑定端口。
    失败语义：当无可用端口时抛 `RuntimeError`。
    副作用：反复尝试绑定端口。
    """
    port = starting_port
    while port < MAX_PORT_NUMBER:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind(("", port))
            except OSError:
                port += 1
            else:
                return port
    msg = "No free ports available"
    raise RuntimeError(msg)


def get_best_access_host(host: str) -> str:
    """将监听地址映射为更适合用户访问的主机名。

    契约：`0.0.0.0`/`::`/空字符串统一映射为 `localhost`。
    失败语义：无。
    副作用：无。
    """
    # 注意：`0.0.0.0` 与 `::` 代表监听所有网卡，不适合作为访问地址展示
    if host in ("0.0.0.0", "::", ""):
        return "localhost"
    return host


def get_api_key() -> str:
    """读取 API Key。

    契约：从环境变量 `LANGFLOW_API_KEY` 读取并返回字符串。
    失败语义：缺失时抛 `ValueError`。
    副作用：读取环境变量。
    """
    api_key = os.getenv("LANGFLOW_API_KEY")
    if not api_key:
        msg = "LANGFLOW_API_KEY environment variable is not set"
        raise ValueError(msg)
    return api_key


def is_url(path_or_url: str) -> bool:
    """判断字符串是否为 URL。

    契约：仅当解析后包含 scheme 与 netloc 时返回 True。
    失败语义：解析异常时返回 False。
    副作用：无。
    """
    try:
        result = urlparse(path_or_url)
        return all([result.scheme, result.netloc])
    except Exception:  # noqa: BLE001
        return False


def download_script_from_url(url: str, verbose_print) -> Path:
    """下载脚本并保存到临时文件。

    契约：仅下载文本脚本内容并写入临时 `.py` 文件，返回其路径。
    失败语义：HTTP/网络/解析错误时抛 `typer.Exit(1)`。
    副作用：发起网络请求并创建临时文件。

    关键路径（三步）：
    1) 发起 HTTP 请求并校验状态码
    2) 将响应文本写入临时 `.py`
    3) 返回临时文件路径
    """
    verbose_print(f"Downloading script from URL: {url}")

    try:
        with httpx.Client(timeout=30.0) as client:
            response = client.get(url)
            response.raise_for_status()

            content_type = response.headers.get("content-type", "").lower()
            valid_types = {"application/x-python", "application/octet-stream"}
            if not (content_type.startswith("text/") or content_type in valid_types):
                verbose_print(f"Warning: Unexpected content type: {content_type}")

            with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as temp_file:
                temp_path = Path(temp_file.name)

                script_content = response.text
                temp_file.write(script_content)

            verbose_print(f"✓ Script downloaded successfully to temporary file: {temp_path}")
            return temp_path

    except httpx.HTTPStatusError as e:
        msg = f"✗ HTTP error downloading script: {e.response.status_code} - {e.response.text}"
        verbose_print(msg)
        raise typer.Exit(1) from e
    except httpx.RequestError as e:
        msg = f"✗ Network error downloading script: {e}"
        verbose_print(msg)
        raise typer.Exit(1) from e
    except Exception as e:
        msg = f"✗ Unexpected error downloading script: {e}"
        verbose_print(msg)
        raise typer.Exit(1) from e


def validate_script_path(script_path: Path | str, verbose_print) -> tuple[str, Path]:
    """校验脚本路径或 URL 并返回解析结果。

    契约：支持本地 `.py/.json` 或 URL；返回 `(file_extension, resolved_path)`。
    失败语义：路径非法或扩展名不支持时抛 `typer.Exit(1)`。
    副作用：URL 情况下会下载并生成临时文件。

    关键路径（三步）：
    1) 判断输入是否为 URL 并下载
    2) 校验路径存在性与类型
    3) 校验扩展名并返回结果
    """
    # 注意：URL 会先下载再校验扩展名
    if isinstance(script_path, str) and is_url(script_path):
        resolved_path = download_script_from_url(script_path, verbose_print)
        file_extension = resolved_path.suffix.lower()
        if file_extension != ".py":
            verbose_print(f"Error: URL must point to a Python script (.py file), got: {file_extension}")
            raise typer.Exit(1)
        return file_extension, resolved_path

    if isinstance(script_path, str):
        script_path = Path(script_path)

    if not script_path.exists():
        verbose_print(f"Error: File '{script_path}' does not exist.")
        raise typer.Exit(1)

    if not script_path.is_file():
        verbose_print(f"Error: '{script_path}' is not a file.")
        raise typer.Exit(1)

    file_extension = script_path.suffix.lower()
    if file_extension not in [".py", ".json"]:
        verbose_print(f"Error: '{script_path}' must be a .py or .json file.")
        raise typer.Exit(1)

    return file_extension, script_path


async def load_graph_from_path(script_path: Path, file_extension: str, verbose_print, *, verbose: bool = False):
    """从脚本或 JSON 文件加载图对象。

    契约：`.py` 通过脚本执行提取图，`.json` 直接加载 flow。
    失败语义：解析/执行失败时抛 `typer.Exit(1)`。
    副作用：执行脚本或读取文件，可能触发组件初始化。

    关键路径（三步）：
    1) 根据扩展名选择脚本或 JSON 加载路径
    2) 解析脚本并提取 `graph` 或加载 JSON
    3) 返回图对象或抛出退出异常

    排障入口：`verbose_print` 输出与异常消息。
    """
    file_type = "Python script" if file_extension == ".py" else "JSON flow"
    verbose_print(f"Analyzing {file_type}: {script_path}")

    try:
        if file_extension == ".py":
            verbose_print("Analyzing Python script...")
            graph_var = find_graph_variable(script_path)
            if graph_var:
                source_info = graph_var.get("source", "Unknown")
                type_info = graph_var.get("type", "Unknown")
                line_no = graph_var.get("line", "Unknown")
                verbose_print(f"✓ Found 'graph' variable at line {line_no}")
                verbose_print(f"  Type: {type_info}")
                verbose_print(f"  Source: {source_info}")
            else:
                error_msg = "No 'graph' variable found in script"
                verbose_print(f"✗ {error_msg}")
                raise ValueError(error_msg)

            verbose_print("Loading graph...")
            graph = await load_graph_from_script(script_path)
        else:
            verbose_print("Loading JSON flow...")
            graph = load_flow_from_json(script_path, disable_logs=not verbose)

    except ValueError as e:
        raise typer.Exit(1) from e
    except Exception as e:
        verbose_print(f"✗ Failed to load graph: {e}")
        raise typer.Exit(1) from e
    else:
        return graph


def prepare_graph(graph, verbose_print):
    """准备图对象以便执行。

    契约：调用 `graph.prepare()` 完成依赖解析与初始化。
    失败语义：准备失败时抛 `typer.Exit(1)`。
    副作用：修改图内部状态。
    """
    verbose_print("Preparing graph for execution...")
    try:
        graph.prepare()
        verbose_print("✓ Graph prepared successfully")
    except Exception as e:
        verbose_print(f"✗ Failed to prepare graph: {e}")
        raise typer.Exit(1) from e


async def execute_graph_with_capture(graph, input_value: str | None):
    """执行图并捕获 stdout/stderr。

    契约：返回 `(results, captured_logs)`；`captured_logs` 由 stdout+stderr 拼接。
    失败语义：执行异常会原样抛出，且异常消息可能包含捕获的 stderr。
    副作用：临时替换 `sys.stdout` 与 `sys.stderr`。

    关键路径（三步）：
    1) 组装 `InputValueRequest`
    2) 重定向 stdout/stderr 并执行 `graph.async_start`
    3) 恢复标准输出并返回结果与日志
    """
    inputs = InputValueRequest(input_value=input_value) if input_value else None

    captured_stdout = StringIO()
    captured_stderr = StringIO()

    original_stdout = sys.stdout
    original_stderr = sys.stderr

    try:
        sys.stdout = captured_stdout
        sys.stderr = captured_stderr
        results = [result async for result in graph.async_start(inputs)]
    except Exception as exc:
        error_output = captured_stderr.getvalue()
        if error_output:
            # 注意：将 stderr 内容拼接到异常信息中，便于 CLI 排障
            exc.args = (f"{exc.args[0] if exc.args else str(exc)}\n\nCaptured stderr:\n{error_output}",)
        raise
    finally:
        sys.stdout = original_stdout
        sys.stderr = original_stderr

    captured_logs = captured_stdout.getvalue() + captured_stderr.getvalue()

    return results, captured_logs


def extract_result_data(results, captured_logs: str) -> dict:
    """从执行结果中抽取结构化输出。

    契约：返回字典包含 `logs` 字段与结构化结果。
    失败语义：依赖 `extract_structured_result` 的返回；此函数不抛错。
    副作用：无。
    """
    result_data = extract_structured_result(results)
    result_data["logs"] = captured_logs
    return result_data


# --- 依赖解析工具 --------------------------------------------------------------------------


def _parse_pep723_block(script_path: Path, verbose_print) -> dict | None:
    """解析 PEP-723 内联依赖块。

    契约：若存在 `# /// script` 块则解析并返回 TOML 字典，否则返回 None。
    失败语义：读取或解析失败时返回 None，并输出诊断信息。
    副作用：读取脚本文本。

    关键路径（三步）：
    1) 读取脚本并定位 `# /// script` 块
    2) 清理注释前缀并组装 TOML 文本
    3) 调用解析器返回字典

    排障入口：`verbose_print` 输出的解析失败信息。
    """
    if _toml_parser is None:
        verbose_print("tomllib/tomli not available - cannot parse inline dependencies")
        return None

    try:
        lines = script_path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:  # pragma: no cover
        verbose_print(f"Failed reading script for dependency parsing: {exc}")
        return None

    try:
        start_idx = next(i for i, ln in enumerate(lines) if ln.lstrip().startswith("# /// script")) + 1
        end_idx = next(i for i, ln in enumerate(lines[start_idx:], start=start_idx) if ln.lstrip().startswith("# ///"))
    except StopIteration:
        return None

    block_lines: list[str] = []
    for raw_line in lines[start_idx:end_idx]:
        stripped_line = raw_line.lstrip()
        if not stripped_line.startswith("#"):
            continue
        block_lines.append(stripped_line.lstrip("# "))

    block_toml = "\n".join(block_lines).strip()
    if not block_toml:
        return None

    try:
        return _toml_parser.loads(block_toml)
    except Exception as exc:  # pragma: no cover  # noqa: BLE001
        verbose_print(f"Failed parsing TOML from PEP-723 block: {exc}")
        return None


def extract_script_dependencies(script_path: Path, verbose_print) -> list[str]:
    """读取脚本的 PEP-723 依赖声明。

    契约：仅支持 `.py` 文件；无元数据或解析失败时返回空列表。
    失败语义：解析异常被视为无依赖，不抛错。
    副作用：读取脚本文本。
    """
    if script_path.suffix != ".py":
        return []

    parsed = _parse_pep723_block(script_path, verbose_print)
    if not parsed:
        return []

    deps = parsed.get("dependencies", [])
    if isinstance(deps, list):
        return [str(d).strip() for d in deps if str(d).strip()]
    return []


def _needs_install(requirement: str) -> bool:
    """判断依赖是否可能缺失（启发式）。

    契约：尽力通过 `importlib.metadata` 判断版本是否满足；无法判断时返回 True。
    失败语义：解析失败或版本无法比较时视为缺失。
    副作用：读取已安装包元数据。

    关键路径（三步）：
    1) 解析 PEP 508 依赖字符串
    2) 查询已安装版本并进行比对
    3) 返回是否需要安装
    """
    # 注意：延迟导入以避免未使用时引入额外依赖
    from packaging.requirements import Requirement

    try:
        req = Requirement(requirement)
    except Exception:  # noqa: BLE001
        return True

    try:
        dist_version = importlib_metadata.version(req.name)
    except importlib_metadata.PackageNotFoundError:
        return True

    if not req.specifier:
        return False

    try:
        from packaging.version import InvalidVersion, Version
    except ImportError:
        return True

    try:
        if req.specifier.contains(Version(dist_version), prereleases=True):
            return False
    except InvalidVersion:
        return True

    return True


def ensure_dependencies_installed(dependencies: list[str], verbose_print) -> None:
    """安装缺失的脚本依赖（优先 uv，其次 pip）。

    契约：仅对缺失依赖执行安装；若全部满足则直接返回。
    失败语义：安装失败抛 `typer.Exit(1)`。
    副作用：执行子进程安装依赖。

    关键路径（三步）：
    1) 过滤已安装依赖
    2) 选择 uv 或 pip 作为安装器
    3) 执行安装并处理失败
    """
    if not dependencies:
        return

    missing = [req for req in dependencies if _needs_install(req)]
    if not missing:
        verbose_print("All script dependencies already satisfied")
        return

    installer_cmd: list[str]
    if which("uv"):
        installer_cmd = ["uv", "pip", "install", "--quiet", *missing]
        tool_name = "uv"
    else:
        installer_cmd = [sys.executable, "-m", "pip", "install", "--quiet", *missing]
        tool_name = "pip"

    verbose_print(f"Installing missing dependencies with {tool_name}: {', '.join(missing)}")
    try:
        subprocess.run(installer_cmd, check=True)  # noqa: S603
        verbose_print("✓ Dependency installation succeeded")
    except subprocess.CalledProcessError as exc:  # pragma: no cover
        verbose_print(f"✗ Failed installing dependencies: {exc}")
        raise typer.Exit(1) from exc


def flow_id_from_path(file_path: Path, root_dir: Path) -> str:
    """从相对路径生成稳定的 UUIDv5。

    契约：使用固定命名空间与 `file_path` 相对 `root_dir` 的 POSIX 路径生成 ID。
    失败语义：`file_path` 不在 `root_dir` 下时 `relative_to` 会抛 `ValueError`。
    副作用：无。
    """
    relative = file_path.relative_to(root_dir).as_posix()
    return str(uuid.uuid5(_LANGFLOW_NAMESPACE_UUID, relative))


# ---------------------------------------------------------------------------
# GitHub / ZIP 仓库工具（initial_setup 的同步版本）
# ---------------------------------------------------------------------------

_GITHUB_RE_REPO = re.compile(r"https?://(?:www\.)?github\.com/([\w.-]+)/([\w.-]+)(?:\.git)?/?$")
_GITHUB_RE_TREE = re.compile(r"https?://(?:www\.)?github\.com/([\w.-]+)/([\w.-]+)/tree/([\w\/-]+)")
_GITHUB_RE_RELEASE = re.compile(r"https?://(?:www\.)?github\.com/([\w.-]+)/([\w.-]+)/releases/tag/([\w\/-]+)")
_GITHUB_RE_COMMIT = re.compile(r"https?://(?:www\.)?github\.com/([\w.-]+)/([\w.-]+)/commit/(\w+)(?:/)?$")


def _github_headers() -> dict[str, str]:
    """构造 GitHub API 请求头。

    契约：若存在 `GITHUB_TOKEN` 则返回包含 Authorization 的头，否则返回空字典。
    失败语义：无。
    副作用：读取环境变量。
    """
    token = os.getenv(_GITHUB_TOKEN_ENV)
    if token:
        return {"Authorization": f"token {token}"}
    return {}


def detect_github_url_sync(url: str, *, timeout: float = 15.0) -> str:
    """将 GitHub URL 归一化为可下载的 `.zip` 链接（同步）。

    契约：支持仓库/分支/Tag/Commit 链接，返回对应的 ZIP 下载 URL。
    失败语义：GitHub API 请求失败会抛出异常。
    副作用：可能调用 GitHub API 获取默认分支。

    关键路径（三步）：
    1) 匹配 URL 类型（仓库/分支/Tag/Commit）
    2) 必要时调用 GitHub API 获取默认分支
    3) 生成并返回 ZIP 下载链接
    """
    if match := _GITHUB_RE_REPO.match(url):
        owner, repo = match.groups()
        with httpx.Client(timeout=timeout, follow_redirects=True, headers=_github_headers()) as client:
            resp = client.get(f"https://api.github.com/repos/{owner}/{repo}")
            resp.raise_for_status()
            default_branch = resp.json().get("default_branch", "main")
        return f"https://github.com/{owner}/{repo}/archive/refs/heads/{default_branch}.zip"

    if match := _GITHUB_RE_TREE.match(url):
        owner, repo, branch = match.groups()
        branch = branch.rstrip("/")
        return f"https://github.com/{owner}/{repo}/archive/refs/heads/{branch}.zip"

    if match := _GITHUB_RE_RELEASE.match(url):
        owner, repo, tag = match.groups()
        tag = tag.rstrip("/")
        return f"https://github.com/{owner}/{repo}/archive/refs/tags/{tag}.zip"

    if match := _GITHUB_RE_COMMIT.match(url):
        owner, repo, commit = match.groups()
        return f"https://github.com/{owner}/{repo}/archive/{commit}.zip"

    return url


def download_and_extract_repo(url: str, verbose_print, *, timeout: float = 60.0) -> Path:
    """下载 ZIP 并解压到临时目录。

    契约：返回包含解压文件的根目录路径。
    失败语义：HTTP 或解压错误会抛异常。
    副作用：网络请求、写入临时目录、修改 `sys.path`。

    关键路径（三步）：
    1) 将 URL 归一化为 ZIP 下载地址
    2) 下载并解压到临时目录
    3) 调整 `sys.path` 并返回根目录
    """
    verbose_print(f"Downloading repository/ZIP from {url}")

    zip_url = detect_github_url_sync(url)

    try:
        with httpx.Client(timeout=timeout, follow_redirects=True, headers=_github_headers()) as client:
            resp = client.get(zip_url)
            resp.raise_for_status()

        tmp_dir = tempfile.TemporaryDirectory()
        with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
            zf.extractall(tmp_dir.name)

        verbose_print(f"✓ Repository extracted to {tmp_dir.name}")

        root_path = Path(tmp_dir.name)
        sub_entries = list(root_path.iterdir())
        if len(sub_entries) == 1 and sub_entries[0].is_dir():
            root_path = sub_entries[0]

        # 注意：自定义组件加载需要根目录在 sys.path
        if str(root_path) not in sys.path:
            sys.path.insert(0, str(root_path))

        # 注意：将 TemporaryDirectory 绑定到返回对象，防止提前清理
        root_path._tmp_dir = tmp_dir  # type: ignore[attr-defined]  # noqa: SLF001

    except httpx.HTTPStatusError as e:
        verbose_print(f"✗ HTTP error downloading ZIP: {e.response.status_code}")
        raise
    except Exception as exc:
        verbose_print(f"✗ Failed downloading or extracting repo: {exc}")
        raise
    else:
        return root_path


def extract_script_docstring(script_path: Path) -> str | None:
    """提取脚本的模块级 docstring。

    契约：返回首个模块级 docstring 文本，未找到则返回 None。
    失败语义：读取/解析失败时返回 None。
    副作用：读取文件并解析 AST。

    关键路径（三步）：
    1) 读取脚本文本并解析 AST
    2) 判断首个语句是否为 docstring
    3) 返回清理后的文本或 None
    """
    try:
        with script_path.open(encoding="utf-8") as f:
            content = f.read()

        tree = ast.parse(content)

        if (
            tree.body
            and isinstance(tree.body[0], ast.Expr)
            and isinstance(tree.body[0].value, ast.Constant)
            and isinstance(tree.body[0].value.value, str)
        ):
            docstring = tree.body[0].value.value
            return docstring.strip()

    except (OSError, SyntaxError, UnicodeDecodeError):
        pass

    return None
