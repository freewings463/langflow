"""
模块名称：CLI 运行命令包装

本模块提供 `lfx run` 的 CLI 封装，主要用于加载并执行单个 flow/script 并输出结果。主要功能包括：
- 解析 CLI 选项并调用 `run_flow`
- 处理输出格式与错误包装
- 提示 LangChain 版本不兼容原因

关键组件：
- `run`：CLI 入口
- `_check_langchain_version_compatibility`：版本兼容性诊断

设计背景：在容器/脚本场景提供最小输出与清晰错误信息。
注意事项：默认输出偏简洁，开启 `-v/-vv/-vvv` 可获得更多诊断信息。
"""

import json
from functools import partial
from pathlib import Path

import typer
from asyncer import syncify

from lfx.run.base import RunError, run_flow

VERBOSITY_DETAILED = 2
VERBOSITY_FULL = 3


def _check_langchain_version_compatibility(error_message: str) -> str | None:
    """判断错误是否来自 langchain-core 版本不兼容。

    契约：若检测到不兼容，返回可执行的修复提示；否则返回 None。
    失败语义：无法导入版本信息时仍返回诊断提示（版本记为 unknown）。
    副作用：尝试导入 `langchain_core`。
    """
    # 注意：langchain-core 1.x 移除了 `langchain_core.memory`
    if "langchain_core.memory" in error_message or "No module named 'langchain_core.memory'" in error_message:
        try:
            import langchain_core

            version = getattr(langchain_core, "__version__", "unknown")
        except ImportError:
            version = "unknown"

        return (
            f"ERROR: Incompatible langchain-core version (v{version}).\n\n"
            "The 'langchain_core.memory' module was removed in langchain-core 1.x.\n"
            "lfx requires langchain-core < 1.0.0.\n\n"
            "This usually happens when langchain-openai >= 1.0.0 is installed,\n"
            "which pulls in langchain-core >= 1.0.0.\n\n"
            "FIX: Reinstall with compatible versions:\n\n"
            "  uv pip install 'langchain-core>=0.3.0,<1.0.0' \\\n"
            "                 'langchain-openai>=0.3.0,<1.0.0' \\\n"
            "                 'langchain-community>=0.3.0,<1.0.0'\n\n"
            "Or with pip:\n\n"
            "  pip install 'langchain-core>=0.3.0,<1.0.0' \\\n"
            "              'langchain-openai>=0.3.0,<1.0.0' \\\n"
            "              'langchain-community>=0.3.0,<1.0.0'"
        )
    return None


@partial(syncify, raise_sync_error=False)
async def run(
    script_path: Path | None = typer.Argument(  # noqa: B008
        None, help="Path to the Python script (.py) or JSON flow (.json) containing a graph"
    ),
    input_value: str | None = typer.Argument(None, help="Input value to pass to the graph"),
    input_value_option: str | None = typer.Option(
        None,
        "--input-value",
        help="Input value to pass to the graph (alternative to positional argument)",
    ),
    output_format: str = typer.Option(
        "json",
        "--format",
        "-f",
        help="Output format: json, text, message, or result",
    ),
    flow_json: str | None = typer.Option(
        None,
        "--flow-json",
        help=("Inline JSON flow content as a string (alternative to script_path)"),
    ),
    *,
    stdin: bool | None = typer.Option(
        default=False,
        flag_value="--stdin",
        show_default=True,
        help="Read JSON flow content from stdin (alternative to script_path)",
    ),
    check_variables: bool = typer.Option(
        default=True,
        show_default=True,
        help="Check global variables for environment compatibility",
    ),
    verbose: bool = typer.Option(
        False,  # noqa: FBT003
        "-v",
        "--verbose",
        help="Show basic progress information",
    ),
    verbose_detailed: bool = typer.Option(
        False,  # noqa: FBT003
        "-vv",
        help="Show detailed progress and debug information",
    ),
    verbose_full: bool = typer.Option(
        False,  # noqa: FBT003
        "-vvv",
        help="Show full debugging output including component logs",
    ),
    timing: bool = typer.Option(
        default=False,
        show_default=True,
        help="Include detailed timing information in output",
    ),
) -> None:
    """执行脚本或 JSON flow 并输出结果。

    契约：支持 `.py`/`.json`/内联 JSON/STDIN；输出格式由 `--format` 控制。
    失败语义：执行失败抛 `RunError` 并转为 JSON 错误输出，随后 `typer.Exit(1)`。
    副作用：执行图、可能触发外部调用与日志输出。

    关键路径（三步）：
    1) 解析输入与输出格式
    2) 调用 `run_flow` 执行并收集结果
    3) 根据格式输出结果或错误
    """
    verbosity = 3 if verbose_full else (2 if verbose_detailed else (1 if verbose else 0))

    try:
        result = await run_flow(
            script_path=script_path,
            input_value=input_value,
            input_value_option=input_value_option,
            output_format=output_format,
            flow_json=flow_json,
            stdin=bool(stdin),
            check_variables=check_variables,
            verbose=verbose,
            verbose_detailed=verbose_detailed,
            verbose_full=verbose_full,
            timing=timing,
            global_variables=None,
        )

        if output_format in {"text", "message", "result"}:
            typer.echo(result.get("output", ""))
        else:
            indent = 2 if verbosity > 0 else None
            typer.echo(json.dumps(result, indent=indent))

    except RunError as e:
        error_response = {
            "success": False,
            "type": "error",
        }
        if e.original_exception:
            error_response["exception_type"] = type(e.original_exception).__name__
            error_response["exception_message"] = str(e.original_exception)
        else:
            error_response["exception_message"] = str(e)
        typer.echo(json.dumps(error_response))
        raise typer.Exit(1) from e
