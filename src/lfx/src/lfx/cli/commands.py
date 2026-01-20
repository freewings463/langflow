"""
模块名称：LFX CLI 命令实现

本模块提供 LFX 命令行的核心子命令实现，主要用于将单个 flow 以 HTTP API 形式对外提供服务。主要功能包括：
- 校验输入来源（文件/内联 JSON/STDIN）
- 加载并准备图对象
- 启动 FastAPI + Uvicorn 服务并输出使用提示

关键组件：
- `serve_command`：CLI `lfx serve` 的主入口

设计背景：CLI 需要提供统一的部署入口，并在错误输入/环境不完整时快速失败。
注意事项：启动服务前必须配置 `LANGFLOW_API_KEY`，否则直接退出。
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from functools import partial
from pathlib import Path

import typer
import uvicorn
from asyncer import syncify
from dotenv import load_dotenv
from rich.console import Console
from rich.panel import Panel

from lfx.cli.common import (
    create_verbose_printer,
    flow_id_from_path,
    get_api_key,
    get_best_access_host,
    get_free_port,
    is_port_in_use,
    load_graph_from_path,
)
from lfx.cli.serve_app import FlowMeta, create_multi_serve_app

console = Console()

API_KEY_MASK_LENGTH = 8


@partial(syncify, raise_sync_error=False)
async def serve_command(
    script_path: str | None = typer.Argument(
        None,
        help=(
            "Path to JSON flow (.json) or Python script (.py) file or stdin input. "
            "Optional when using --flow-json or --stdin."
        ),
    ),
    host: str = typer.Option("127.0.0.1", "--host", "-h", help="Host to bind the server to"),
    port: int = typer.Option(8000, "--port", "-p", help="Port to bind the server to"),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Show diagnostic output and execution details"),  # noqa: FBT001, FBT003
    env_file: Path | None = typer.Option(
        None,
        "--env-file",
        help="Path to the .env file containing environment variables",
    ),
    log_level: str = typer.Option(
        "warning",
        "--log-level",
        help="Logging level. One of: debug, info, warning, error, critical",
    ),
    flow_json: str | None = typer.Option(
        None,
        "--flow-json",
        help="Inline JSON flow content as a string (alternative to script_path)",
    ),
    *,
    stdin: bool = typer.Option(
        False,  # noqa: FBT003
        "--stdin",
        help="Read JSON flow content from stdin (alternative to script_path)",
    ),
    check_variables: bool = typer.Option(
        True,  # noqa: FBT003
        "--check-variables/--no-check-variables",
        help="Check global variables for environment compatibility",
    ),
) -> None:
    """以 HTTP API 形式运行单个 LFX flow。

    契约：`script_path`/`--flow-json`/`--stdin` 三者必须且仅能提供一种；成功后监听 `host:port`。
    失败语义：输入冲突、JSON 解析失败、API Key 缺失或图准备失败时抛 `typer.Exit(1)`。
    副作用：读取文件/STDIN、创建临时文件、启动 Uvicorn 进程监听端口。

    关键路径（三步）：
    1) 校验输入来源与环境变量，并加载 `.env`
    2) 解析并准备图对象（含可选的全局变量校验）
    3) 构建 FastAPI 应用并启动 Uvicorn 服务

    异常流：JSON 语法错误、`LANGFLOW_API_KEY` 缺失、图准备失败会直接退出。
    排障入口：`--verbose` 输出、`lfx.log.logger` 日志与 Uvicorn 日志级别。
    """
    # 导入时配置日志，避免 CLI 启动时额外依赖
    from lfx.log.logger import configure, logger

    configure(log_level=log_level)

    verbose_print = create_verbose_printer(verbose=verbose)

    # 注意：三种输入源必须且仅能选择一种
    input_sources = [script_path is not None, flow_json is not None, stdin]
    if sum(input_sources) != 1:
        if sum(input_sources) == 0:
            verbose_print("Error: Must provide either script_path, --flow-json, or --stdin")
        else:
            verbose_print("Error: Cannot use script_path, --flow-json, and --stdin together. Choose exactly one.")
        raise typer.Exit(1)

    if env_file:
        if not env_file.exists():
            verbose_print(f"Error: Environment file '{env_file}' does not exist.")
            raise typer.Exit(1)

        verbose_print(f"Loading environment variables from: {env_file}")
        load_dotenv(env_file)

    try:
        api_key = get_api_key()
        verbose_print("✓ LANGFLOW_API_KEY is configured")
    except ValueError as e:
        typer.echo(f"✗ {e}", err=True)
        typer.echo("Set the LANGFLOW_API_KEY environment variable before serving.", err=True)
        raise typer.Exit(1) from e

    valid_log_levels = {"debug", "info", "warning", "error", "critical"}
    if log_level.lower() not in valid_log_levels:
        verbose_print(f"Error: Invalid log level '{log_level}'. Must be one of: {', '.join(sorted(valid_log_levels))}")
        raise typer.Exit(1)

    # 注意：关闭 pretty logs，避免 API 响应夹带 ANSI 控制符
    os.environ["LANGFLOW_PRETTY_LOGS"] = "false"
    verbose_print(f"Configuring logging with level: {log_level}")
    from lfx.log.logger import configure

    configure(log_level=log_level)

    # 处理内联 JSON 或 STDIN 输入
    temp_file_to_cleanup = None

    if flow_json is not None:
        logger.info("Processing inline JSON content...")
        try:
            json_data = json.loads(flow_json)
            logger.info("JSON content is valid")

            # 注意：为复用后续加载逻辑，内联 JSON 会落盘到临时文件
            with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as temp_file:
                json.dump(json_data, temp_file, indent=2)
                temp_file_to_cleanup = temp_file.name

            script_path = temp_file_to_cleanup
            logger.info(f"Created temporary file: {script_path}")

        except json.JSONDecodeError as e:
            typer.echo(f"Error: Invalid JSON content: {e}", err=True)
            raise typer.Exit(1) from e
        except Exception as e:
            verbose_print(f"Error processing JSON content: {e}")
            raise typer.Exit(1) from e

    elif stdin:
        logger.info("Reading JSON content from stdin...")
        try:
            stdin_content = sys.stdin.read().strip()
            if not stdin_content:
                logger.error("No content received from stdin")
                raise typer.Exit(1)

            json_data = json.loads(stdin_content)
            logger.info("JSON content from stdin is valid")

            # 注意：STDIN 内容写入临时文件以复用加载路径
            with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as temp_file:
                json.dump(json_data, temp_file, indent=2)
                temp_file_to_cleanup = temp_file.name

            script_path = temp_file_to_cleanup
            logger.info(f"Created temporary file from stdin: {script_path}")

        except json.JSONDecodeError as e:
            verbose_print(f"Error: Invalid JSON content from stdin: {e}")
            raise typer.Exit(1) from e
        except Exception as e:
            verbose_print(f"Error reading from stdin: {e}")
            raise typer.Exit(1) from e

    try:
        if script_path is None:
            verbose_print("Error: script_path is None after input validation")
            raise typer.Exit(1)

        resolved_path = Path(script_path).resolve()

        if not resolved_path.exists():
            typer.echo(f"Error: File '{resolved_path}' does not exist.", err=True)
            raise typer.Exit(1)

        if resolved_path.suffix == ".json":
            graph = await load_graph_from_path(resolved_path, resolved_path.suffix, verbose_print, verbose=verbose)
        elif resolved_path.suffix == ".py":
            verbose_print("Loading graph from Python script...")
            from lfx.cli.script_loader import load_graph_from_script

            graph = await load_graph_from_script(resolved_path)
            verbose_print("✓ Graph loaded from Python script")
        else:
            err_msg = "Error: Only JSON flow files (.json) or Python scripts (.py) are supported. "
            err_msg += f"Got: {resolved_path.suffix}"
            verbose_print(err_msg)
            raise typer.Exit(1)

        logger.info("Preparing graph for serving...")
        try:
            graph.prepare()
            logger.info("Graph prepared successfully")

            if check_variables:
                from lfx.cli.validation import validate_global_variables_for_env

                validation_errors = validate_global_variables_for_env(graph)
                if validation_errors:
                    logger.error("Global variable validation failed:")
                    for error in validation_errors:
                        logger.error(f"  - {error}")
                    raise typer.Exit(1)
            else:
                logger.info("Global variable validation skipped")
        except Exception as e:
            verbose_print(f"✗ Failed to prepare graph: {e}")
            raise typer.Exit(1) from e

        if is_port_in_use(port, host):
            available_port = get_free_port(port)
            if verbose:
                verbose_print(f"Port {port} is in use, using port {available_port} instead")
            port = available_port

        flow_id = flow_id_from_path(resolved_path, resolved_path.parent)
        graph.flow_id = flow_id  # 注意：在图对象上标注 flow_id 便于后续日志与追踪

        title = resolved_path.stem
        description = None

        metas = {
            flow_id: FlowMeta(
                id=flow_id,
                relative_path=str(resolved_path.name),
                title=title,
                description=description,
            )
        }
        graphs = {flow_id: graph}

        source_display = "inline JSON" if flow_json else "stdin" if stdin else str(resolved_path)
        verbose_print(f"✓ Prepared single flow '{title}' from {source_display} (id={flow_id})")

        serve_app = create_multi_serve_app(
            root_dir=resolved_path.parent,
            graphs=graphs,
            metas=metas,
            verbose_print=verbose_print,
        )

        verbose_print("🚀 Starting single-flow server...")

        protocol = "http"
        access_host = get_best_access_host(host)

        masked_key = f"{api_key[:API_KEY_MASK_LENGTH]}..." if len(api_key) > API_KEY_MASK_LENGTH else "***"

        console.print()
        console.print(
            Panel.fit(
                f"[bold green]🎯 Single Flow Served Successfully![/bold green]\n\n"
                f"[bold]Source:[/bold] {source_display}\n"
                f"[bold]Server:[/bold] {protocol}://{access_host}:{port}\n"
                f"[bold]API Key:[/bold] {masked_key}\n\n"
                f"[dim]Send POST requests to:[/dim]\n"
                f"[blue]{protocol}://{access_host}:{port}/flows/{flow_id}/run[/blue]\n\n"
                f"[dim]With headers:[/dim]\n"
                f"[blue]x-api-key: {masked_key}[/blue]\n\n"
                f"[dim]Or query parameter:[/dim]\n"
                f"[blue]?x-api-key={masked_key}[/blue]\n\n"
                f"[dim]Request body:[/dim]\n"
                f"[blue]{{'input_value': 'Your input message'}}[/blue]",
                title="[bold blue]LFX Server[/bold blue]",
                border_style="blue",
            )
        )
        console.print()

        # 决策：使用 `uvicorn.Server` 而非 `uvicorn.run`
        # 问题：`uvicorn.run` 内部调用 `asyncio.run()`，会在已有事件循环时失败
        # 方案：直接构造 `uvicorn.Server` 并 `await serve()` 以复用当前循环
        # 代价：需要显式构建 `Config` 与 `Server`，代码更冗长
        # 重评：若未来移除 `syncify` 或统一事件循环管理，可评估回退到 `uvicorn.run`
        try:
            config = uvicorn.Config(
                serve_app,
                host=host,
                port=port,
                log_level=log_level,
            )
            server = uvicorn.Server(config)
            await server.serve()
        except KeyboardInterrupt:
            verbose_print("\n👋 Server stopped")
            raise typer.Exit(0) from None
        except Exception as e:
            verbose_print(f"✗ Failed to start server: {e}")
            raise typer.Exit(1) from e

    finally:
        # 注意：仅清理由内联/STDIN 生成的临时文件
        if temp_file_to_cleanup:
            try:
                Path(temp_file_to_cleanup).unlink()
                verbose_print(f"✓ Cleaned up temporary file: {temp_file_to_cleanup}")
            except OSError as e:
                verbose_print(f"Warning: Failed to clean up temporary file {temp_file_to_cleanup}: {e}")
