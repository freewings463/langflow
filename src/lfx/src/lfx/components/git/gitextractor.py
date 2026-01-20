"""
模块名称：gitextractor

本模块提供 Git 仓库抽取组件，支持获取仓库信息、结构与文件内容。
主要功能包括：
- 克隆仓库到临时目录并抽取元数据
- 统计文件数量/大小/行数
- 输出目录结构与文件内容

关键组件：
- `GitExtractorComponent`：仓库抽取组件

设计背景：需要对远程仓库进行结构化分析与内容提取
使用场景：快速审计仓库内容或生成摘要输入
注意事项：克隆过程依赖网络与权限，且会消耗临时磁盘空间
"""

import os
import shutil
import tempfile
from contextlib import asynccontextmanager
from pathlib import Path

import aiofiles
import git

from lfx.custom.custom_component.component import Component
from lfx.io import MessageTextInput, Output
from lfx.schema.data import Data
from lfx.schema.message import Message


class GitExtractorComponent(Component):
    """Git 仓库抽取组件。

    契约：输入 `repository_url`，输出多路仓库信息与内容。
    副作用：克隆仓库到临时目录并读取文件内容。
    失败语义：Git 操作失败时返回带 `error` 的 `Data` 或 `Message`。
    决策：每个方法独立克隆，保证互不干扰。
    问题：并发调用时共享同一克隆目录易产生冲突。
    方案：使用临时目录在每次调用中独立克隆。
    代价：重复克隆带来时间与带宽开销。
    重评：当需要缓存或复用克隆结果时。
    """
    display_name = "GitExtractor"
    description = "Analyzes a Git repository and returns file contents and complete repository information"
    icon = "GitLoader"

    inputs = [
        MessageTextInput(
            name="repository_url",
            display_name="Repository URL",
            info="URL of the Git repository (e.g., https://github.com/username/repo)",
            value="",
        ),
    ]

    outputs = [
        Output(
            display_name="Text-Based File Contents",
            name="text_based_file_contents",
            method="get_text_based_file_contents",
        ),
        Output(display_name="Directory Structure", name="directory_structure", method="get_directory_structure"),
        Output(display_name="Repository Info", name="repository_info", method="get_repository_info"),
        Output(display_name="Statistics", name="statistics", method="get_statistics"),
        Output(display_name="Files Content", name="files_content", method="get_files_content"),
    ]

    @asynccontextmanager
    async def temp_git_repo(self):
        """临时 Git 仓库克隆上下文管理器。

        契约：在 `yield` 期间提供可用的仓库路径。
        副作用：创建并删除临时目录。
        失败语义：克隆失败抛 `git.GitError`。
        """
        temp_dir = tempfile.mkdtemp()
        try:
            # 注意：克隆为同步调用，确保异常可清理临时目录。
            git.Repo.clone_from(self.repository_url, temp_dir)
            yield temp_dir
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

    async def get_repository_info(self) -> list[Data]:
        """获取仓库基础信息。

        契约：返回包含默认分支、最近提交等信息的 `Data` 列表。
        副作用：克隆仓库并读取 Git 元数据。
        失败语义：Git 异常时返回 `error` 结果。
        """
        try:
            async with self.temp_git_repo() as temp_dir:
                repo = git.Repo(temp_dir)
                repo_info = {
                    "name": self.repository_url.split("/")[-1],
                    "url": self.repository_url,
                    "default_branch": repo.active_branch.name,
                    "remote_urls": [remote.url for remote in repo.remotes],
                    "last_commit": {
                        "hash": repo.head.commit.hexsha,
                        "author": str(repo.head.commit.author),
                        "message": repo.head.commit.message.strip(),
                        "date": str(repo.head.commit.committed_datetime),
                    },
                    "branches": [str(branch) for branch in repo.branches],
                }
                result = [Data(data=repo_info)]
                self.status = result
                return result
        except git.GitError as e:
            error_result = [Data(data={"error": f"Error getting repository info: {e!s}"})]
            self.status = error_result
            return error_result

    async def get_statistics(self) -> list[Data]:
        """统计仓库文件数量、体积与行数。

        契约：返回统计字段的 `Data` 列表。
        副作用：遍历仓库文件并读取文本行数。
        失败语义：Git 异常时返回 `error` 结果。
        关键路径（三步）：1) 遍历文件 2) 汇总大小与行数 3) 输出统计。
        性能瓶颈：大仓库遍历与文件读取。
        """
        try:
            async with self.temp_git_repo() as temp_dir:
                total_files = 0
                total_size = 0
                total_lines = 0
                binary_files = 0
                directories = 0

                for root, dirs, files in os.walk(temp_dir):
                    total_files += len(files)
                    directories += len(dirs)
                    for file in files:
                        file_path = Path(root) / file
                        total_size += file_path.stat().st_size
                        try:
                            async with aiofiles.open(file_path, encoding="utf-8") as f:
                                total_lines += sum(1 for _ in await f.readlines())
                        except UnicodeDecodeError:
                            binary_files += 1

                statistics = {
                    "total_files": total_files,
                    "total_size_bytes": total_size,
                    "total_size_kb": round(total_size / 1024, 2),
                    "total_size_mb": round(total_size / (1024 * 1024), 2),
                    "total_lines": total_lines,
                    "binary_files": binary_files,
                    "directories": directories,
                }
                result = [Data(data=statistics)]
                self.status = result
                return result
        except git.GitError as e:
            error_result = [Data(data={"error": f"Error calculating statistics: {e!s}"})]
            self.status = error_result
            return error_result

    async def get_directory_structure(self) -> Message:
        """生成目录结构树。

        契约：返回包含目录结构的 `Message`。
        副作用：遍历仓库目录树。
        失败语义：Git 异常时返回错误消息。
        """
        try:
            async with self.temp_git_repo() as temp_dir:
                tree = ["Directory structure:"]
                for root, _dirs, files in os.walk(temp_dir):
                    level = root.replace(temp_dir, "").count(os.sep)
                    indent = "    " * level
                    if level == 0:
                        tree.append(f"└── {Path(root).name}")
                    else:
                        tree.append(f"{indent}├── {Path(root).name}")
                    subindent = "    " * (level + 1)
                    tree.extend(f"{subindent}├── {f}" for f in files)
                directory_structure = "\n".join(tree)
                self.status = directory_structure
                return Message(text=directory_structure)
        except git.GitError as e:
            error_message = f"Error getting directory structure: {e!s}"
            self.status = error_message
            return Message(text=error_message)

    async def get_files_content(self) -> list[Data]:
        """获取所有文件内容（包含二进制占位符）。

        契约：返回包含路径、大小、内容的 `Data` 列表。
        副作用：读取仓库全部文件内容。
        失败语义：Git 异常时返回 `error` 结果。
        关键路径（三步）：1) 遍历文件 2) 读取内容 3) 组装结果。
        性能瓶颈：大文件与大量文件读取。
        """
        try:
            async with self.temp_git_repo() as temp_dir:
                content_list = []
                for root, _, files in os.walk(temp_dir):
                    for file in files:
                        file_path = Path(root) / file
                        relative_path = file_path.relative_to(temp_dir)
                        file_size = file_path.stat().st_size
                        try:
                            async with aiofiles.open(file_path, encoding="utf-8") as f:
                                file_content = await f.read()
                        except UnicodeDecodeError:
                            file_content = "[BINARY FILE]"
                        content_list.append(
                            Data(data={"path": str(relative_path), "size": file_size, "content": file_content})
                        )
                self.status = content_list
                return content_list
        except git.GitError as e:
            error_result = [Data(data={"error": f"Error getting files content: {e!s}"})]
            self.status = error_result
            return error_result

    async def get_text_based_file_contents(self) -> Message:
        """获取文本文件内容（总长度上限 300k 字符）。

        契约：输出拼接后的文本 `Message`，超限会截断。
        副作用：读取文本文件并拼接内容。
        失败语义：Git 异常时返回错误消息。
        关键路径（三步）：1) 遍历文件 2) 读取并累计字符 3) 截断并输出。
        决策：限制总字符数以避免超大输出。
        问题：大仓库输出过大会影响 UI 与传输成本。
        方案：设置 300k 字符上限并截断。
        代价：内容可能不完整。
        重评：当下游支持流式输出或分页时。
        """
        try:
            async with self.temp_git_repo() as temp_dir:
                content_list = ["(Files content cropped to 300k characters, download full ingest to see more)"]
                total_chars = 0
                char_limit = 300000

                for root, _, files in os.walk(temp_dir):
                    for file in files:
                        file_path = Path(root) / file
                        relative_path = file_path.relative_to(temp_dir)
                        content_list.extend(["=" * 50, f"File: /{relative_path}", "=" * 50])

                        try:
                            async with aiofiles.open(file_path, encoding="utf-8") as f:
                                file_content = await f.read()
                                if total_chars + len(file_content) > char_limit:
                                    remaining_chars = char_limit - total_chars
                                    file_content = file_content[:remaining_chars] + "\n... (content truncated)"
                                content_list.append(file_content)
                                total_chars += len(file_content)
                        except UnicodeDecodeError:
                            content_list.append("[BINARY FILE]")

                        content_list.append("")

                        if total_chars >= char_limit:
                            break

                text_content = "\n".join(content_list)
                self.status = text_content
                return Message(text=text_content)
        except git.GitError as e:
            error_message = f"Error getting text-based file contents: {e!s}"
            self.status = error_message
            return Message(text=error_message)
