"""
模块名称：git

本模块提供 Git 仓库加载组件，支持本地路径或远程克隆。
主要功能包括：
- 根据路径/URL 加载仓库文件
- 支持文件名与内容过滤

关键组件：
- `GitLoaderComponent`：仓库加载组件

设计背景：需要将 Git 仓库内容纳入 Langflow 数据流
使用场景：从代码库加载文档/源码用于检索或分析
注意事项：远程克隆会在临时目录执行，受网络与权限影响
"""

import re
import tempfile
from contextlib import asynccontextmanager
from fnmatch import fnmatch
from pathlib import Path

import anyio
from langchain_community.document_loaders.git import GitLoader

from lfx.custom.custom_component.component import Component
from lfx.io import DropdownInput, MessageTextInput, Output
from lfx.schema.data import Data


class GitLoaderComponent(Component):
    """Git 仓库加载组件。

    契约：支持本地路径或远程 URL；输出 `Data` 列表。
    副作用：可能执行克隆、读取文件并写入 `status`。
    失败语义：克隆/读取失败由下游异常抛出。
    决策：用 `GitLoader` 统一处理加载与过滤。
    问题：需要兼容本地与远程仓库加载。
    方案：根据 `repo_source` 选择路径或临时克隆。
    代价：远程克隆存在耗时与空间开销。
    重评：当仓库访问策略变化或需支持镜像缓存时。
    """
    display_name = "Git"
    description = (
        "Load and filter documents from a local or remote Git repository. "
        "Use a local repo path or clone from a remote URL."
    )
    trace_type = "tool"
    icon = "GitLoader"

    inputs = [
        DropdownInput(
            name="repo_source",
            display_name="Repository Source",
            options=["Local", "Remote"],
            required=True,
            info="Select whether to use a local repo path or clone from a remote URL.",
            real_time_refresh=True,
        ),
        MessageTextInput(
            name="repo_path",
            display_name="Local Repository Path",
            required=False,
            info="The local path to the existing Git repository (used if 'Local' is selected).",
            dynamic=True,
            show=False,
        ),
        MessageTextInput(
            name="clone_url",
            display_name="Clone URL",
            required=False,
            info="The URL of the Git repository to clone (used if 'Clone' is selected).",
            dynamic=True,
            show=False,
        ),
        MessageTextInput(
            name="branch",
            display_name="Branch",
            required=False,
            value="main",
            info="The branch to load files from. Defaults to 'main'.",
        ),
        MessageTextInput(
            name="file_filter",
            display_name="File Filter",
            required=False,
            advanced=True,
            info=(
                "Patterns to filter files. For example:\n"
                "Include only .py files: '*.py'\n"
                "Exclude .py files: '!*.py'\n"
                "Multiple patterns can be separated by commas."
            ),
        ),
        MessageTextInput(
            name="content_filter",
            display_name="Content Filter",
            required=False,
            advanced=True,
            info="A regex pattern to filter files based on their content.",
        ),
    ]

    outputs = [
        Output(name="data", display_name="Data", method="load_documents"),
    ]

    @staticmethod
    def is_binary(file_path: str | Path) -> bool:
        """检测文件是否为二进制。

        契约：读取前 1024 字节判断是否含 `\\x00`。
        失败语义：读取失败时按二进制处理。
        """
        try:
            with Path(file_path).open("rb") as file:
                content = file.read(1024)
                return b"\x00" in content
        except Exception:  # noqa: BLE001
            return True

    @staticmethod
    def check_file_patterns(file_path: str | Path, patterns: str) -> bool:
        """按通配符规则判断文件是否应包含。

        契约：`patterns` 支持逗号分隔，`!` 前缀表示排除。
        失败语义：无显式异常。
        关键路径（三步）：1) 解析模式列表 2) 先处理排除 3) 再处理包含。
        """
        if not patterns or patterns.isspace():
            return True

        path_str = str(file_path)
        file_name = Path(path_str).name
        pattern_list: list[str] = [pattern.strip() for pattern in patterns.split(",") if pattern.strip()]

        if not pattern_list:
            return True

        for pattern in pattern_list:
            if pattern.startswith("!"):
                exclude_pattern = pattern[1:]
                if fnmatch(path_str, exclude_pattern) or fnmatch(file_name, exclude_pattern):
                    return False

        include_patterns = [p for p in pattern_list if not p.startswith("!")]
        if not include_patterns:
            return True

        return any(fnmatch(path_str, pattern) or fnmatch(file_name, pattern) for pattern in include_patterns)

    @staticmethod
    def check_content_pattern(file_path: str | Path, pattern: str) -> bool:
        """按正则判断文件内容是否匹配。

        契约：`pattern` 为正则表达式字符串。
        失败语义：正则无效或文件不可读时返回 False。
        关键路径（三步）：1) 二进制检测 2) 编译正则 3) 匹配文本内容。
        """
        try:
            with Path(file_path).open("rb") as file:
                content = file.read(1024)
                if b"\x00" in content:
                    return False

            try:
                content_regex = re.compile(pattern, re.MULTILINE)
                test_str = "test\nstring"
                if not content_regex.search(test_str):
                    pass
            except (re.error, TypeError, ValueError):
                return False

            with Path(file_path).open(encoding="utf-8") as file:
                file_content = file.read()
            return bool(content_regex.search(file_content))
        except (OSError, UnicodeDecodeError):
            return False

    def build_combined_filter(self, file_filter_patterns: str | None = None, content_filter_pattern: str | None = None):
        """组合文件名与内容过滤器。

        契约：返回 `(file_path)->bool` 的过滤函数。
        失败语义：异常时返回 False（排除该文件）。
        关键路径（三步）：1) 校验路径 2) 检查二进制 3) 应用过滤规则。
        决策：二进制文件默认排除。
        问题：二进制内容不适合文本加载与正则匹配。
        方案：检测 `\\x00` 并直接过滤。
        代价：部分可读二进制（如 UTF-16）可能被排除。
        重评：当需要支持更多编码或二进制解析时。
        """

        def combined_filter(file_path: str) -> bool:
            try:
                path = Path(file_path)

                if not path.exists():
                    return False

                if self.is_binary(path):
                    return False

                if file_filter_patterns and not self.check_file_patterns(path, file_filter_patterns):
                    return False

                return not (content_filter_pattern and not self.check_content_pattern(path, content_filter_pattern))
            except Exception:  # noqa: BLE001
                return False

        return combined_filter

    @asynccontextmanager
    async def temp_clone_dir(self):
        """临时克隆目录上下文管理器。

        契约：返回可用的临时目录路径，退出时清理。
        副作用：创建与删除临时目录。
        失败语义：清理失败可能残留目录。
        """
        temp_dir = None
        try:
            temp_dir = tempfile.mkdtemp(prefix="langflow_clone_")
            yield temp_dir
        finally:
            if temp_dir:
                await anyio.Path(temp_dir).rmdir()

    def update_build_config(self, build_config: dict, field_value: str, field_name: str | None = None) -> dict:
        """根据仓库来源切换显示字段。

        契约：仅修改 `build_config` 的显示与必填标志。
        失败语义：无显式异常。
        关键路径（三步）：1) 隐藏所有 2) 判断来源 3) 显示对应字段。
        """
        build_config["repo_path"]["show"] = False
        build_config["clone_url"]["show"] = False

        if field_name == "repo_source":
            if field_value == "Local":
                build_config["repo_path"]["show"] = True
                build_config["repo_path"]["required"] = True
                build_config["clone_url"]["required"] = False
            elif field_value == "Remote":
                build_config["clone_url"]["show"] = True
                build_config["clone_url"]["required"] = True
                build_config["repo_path"]["required"] = False

        return build_config

    async def build_gitloader(self) -> GitLoader:
        """构建 GitLoader 实例。

        契约：`repo_source` 为 Local/Remote，必要字段必须提供。
        副作用：远程模式下创建临时目录用于克隆。
        失败语义：克隆或参数错误由下游异常抛出。
        关键路径（三步）：1) 组合过滤器 2) 解析仓库路径/URL 3) 构建加载器。
        决策：仅在 `branch` 明确设置时传入。
        问题：传空分支可能导致 GitLoader 行为异常。
        方案：空值时传 `None` 让 loader 走默认分支。
        代价：无法区分“用户想用空分支”与“未设置”。
        重评：当 loader 支持显式空分支语义时。
        """
        file_filter_patterns = getattr(self, "file_filter", None)
        content_filter_pattern = getattr(self, "content_filter", None)

        combined_filter = self.build_combined_filter(file_filter_patterns, content_filter_pattern)

        repo_source = getattr(self, "repo_source", None)
        if repo_source == "Local":
            repo_path = self.repo_path
            clone_url = None
        else:
            clone_url = self.clone_url
            async with self.temp_clone_dir() as temp_dir:
                repo_path = temp_dir

        branch = getattr(self, "branch", None)
        if not branch:
            branch = None

        return GitLoader(
            repo_path=repo_path,
            clone_url=clone_url if repo_source == "Remote" else None,
            branch=branch,
            file_filter=combined_filter,
        )

    async def load_documents(self) -> list[Data]:
        """加载 Git 仓库文档并返回 `Data` 列表。

        契约：返回的 `Data` 列表与 loader 产出一致。
        副作用：读取仓库文件并写入 `status`。
        失败语义：加载失败由 loader 异常抛出。
        """
        gitloader = await self.build_gitloader()
        data = [Data.from_document(doc) async for doc in gitloader.alazy_load()]
        self.status = data
        return data
