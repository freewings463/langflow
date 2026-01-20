"""
模块名称：CLI 运行入口（lfx run）

模块目的：作为 Langflow 图的命令行执行入口，负责加载图、校验变量并输出结果。
使用场景：通过 `lfx run` 执行脚本/JSON/stdin 形式的图并返回结构化输出。
主要功能包括：
- 解析输入源并加载 Graph
- 注入全局变量并执行 `prepare()` 校验
- 异步执行并按格式输出结果/日志/耗时

关键组件：
- `RunError`：统一运行异常封装
- `run_flow`：核心执行流程
- `output_error`：错误响应构造器

设计背景：CLI 需要稳定的输入协议与可观测输出，降低排障成本。
注意：verbose 输出统一写入 stderr，避免污染结构化输出。
"""

# 注意：护照用于迁移追溯/治理的元数据；下文注释聚焦于主体执行逻辑与排障信息。

import json
import re
import sys
import time
from io import StringIO
from pathlib import Path

from lfx.cli.script_loader import (
    extract_structured_result,
    extract_text_from_result,
    find_graph_variable,
    load_graph_from_script,
)
from lfx.cli.validation import validate_global_variables_for_env
from lfx.log.logger import logger
from lfx.schema.schema import InputValueRequest

# 详细输出级别常量（与 CLI -v/-vv/-vvv 对齐）
# 注意：使用常量避免魔法数字，便于维护与排障
VERBOSITY_DETAILED = 2  # -vv 级别对应详细输出
VERBOSITY_FULL = 3      # -vvv 级别对应完整输出


class RunError(Exception):
    """运行失败时抛出，用于统一包装并保留底层异常。

    契约：
    - 输入：错误消息字符串和原始异常对象（可选）
    - 输出：封装后的 RunError 实例
    - 副作用：无
    - 失败语义：构造时保存原始异常，便于上层处理
    """

    def __init__(self, message: str, exception: Exception | None = None):
        super().__init__(message)
        self.original_exception = exception


def output_error(error_message: str, *, verbose: bool, exception: Exception | None = None) -> dict:
    """构造可序列化的错误响应。

    契约：
    - 输入：错误消息、详细输出标志、异常对象（可选）
    - 输出：包含错误信息的字典
    - 副作用：verbose=True 时将错误写入 stderr
    - 失败语义：始终返回错误响应字典，不抛出异常

    副作用：`verbose=True` 时会将同一错误写入 stderr（用于 CLI 交互排障）。
    """
    if verbose:
        sys.stderr.write(f"{error_message}\n")

    error_response = {
        "success": False,
        "type": "error",
    }

    # 如有异常对象，则补充可序列化的异常信息（便于 CLI/调用方展示）
    if exception:
        error_response["exception_type"] = type(exception).__name__
        error_response["exception_message"] = str(exception)
    else:
        error_response["exception_message"] = error_message

    return error_response


async def run_flow(
    script_path: Path | None = None,
    input_value: str | None = None,
    input_value_option: str | None = None,
    output_format: str = "json",
    flow_json: str | None = None,
    *,
    stdin: bool = False,
    check_variables: bool = True,
    verbose: bool = False,
    verbose_detailed: bool = False,
    verbose_full: bool = False,
    timing: bool = False,

    global_variables: dict[str, str] | None = None,
) -> dict:
    """执行 Langflow 流程并返回结构化结果（面向 `lfx run`）。

    关键路径（三步）：
    1) 解析唯一输入源并加载图（脚本/JSON/stdin）
    2) 注入全局变量、`prepare()`，并可选执行变量兼容性校验
    3) 异步执行并按 `output_format` 组装输出（可选 `logs`/`timing`）

    异常流：输入源不合法、JSON 解析失败、加载/准备/执行异常均抛 `RunError`。
    性能瓶颈：图执行本身；启用 `timing` 会额外汇总每步耗时。
    排障入口：stderr 日志关键字 `Failed to load graph` / `Failed to execute graph`；json 输出字段 `logs`/`timing`。
    """
    # 按详细级别配置日志（INFO/DEBUG/CRITICAL），并统一写入 stderr 以避免污染结构化输出
    from lfx.log.logger import configure

    if verbose_full:
        configure(log_level="DEBUG", output_file=sys.stderr)
        verbosity = 3
    elif verbose_detailed:
        configure(log_level="DEBUG", output_file=sys.stderr)
        verbosity = 2
    elif verbose:
        configure(log_level="INFO", output_file=sys.stderr)
        verbosity = 1
    else:
        configure(log_level="CRITICAL", output_file=sys.stderr)
        verbosity = 0

    start_time = time.time() if timing else None

    # 注意：同时支持位置参数与 --input-value，位置参数优先以兼容脚本化调用
    final_input_value = input_value or input_value_option

    # 注意：输入源强制三选一，避免 stdin/文件混用导致结果不可复现
    input_sources = [script_path is not None, flow_json is not None, bool(stdin)]
    if sum(input_sources) != 1:
        if sum(input_sources) == 0:
            error_msg = "No input source provided. Must provide either script_path, --flow-json, or --stdin"
        else:
            error_msg = (
                "Multiple input sources provided. Cannot use script_path, --flow-json, and "
                "--stdin together. Choose exactly one."
            )
        output_error(error_msg, verbose=verbose)
        raise RunError(error_msg, None)

    flow_dict: dict | None = None

    if flow_json is not None:
        # 实现：内联 JSON 统一走 json.loads -> dict -> aload_flow_from_json
        # 注意：避免临时文件 I/O 与清理问题；stdin/内联共享 JSONDecodeError 路径便于排障
        if verbosity > 0:
            sys.stderr.write("Processing inline JSON content...\n")
        try:
            flow_dict = json.loads(flow_json)
            if verbosity > 0:
                sys.stderr.write("JSON content is valid\n")
        except json.JSONDecodeError as e:
            error_msg = f"Invalid JSON content: {e}"
            output_error(error_msg, verbose=verbose)
            raise RunError(error_msg, e) from e
        except Exception as e:
            error_msg = f"Error processing JSON content: {e}"
            output_error(error_msg, verbose=verbose)
            raise RunError(error_msg, e) from e
    elif stdin:
        # 注意：stdin 读取到空字符串是常见误用；这里显式报错，避免"静默成功但无结果"
        if verbosity > 0:
            sys.stderr.write("Reading JSON content from stdin...\n")
        try:
            stdin_content = sys.stdin.read().strip()
            if not stdin_content:
                error_msg = "No content received from stdin"
                output_error(error_msg, verbose=verbose)
                raise RunError(error_msg, None)
            flow_dict = json.loads(stdin_content)
            if verbosity > 0:
                sys.stderr.write("JSON content from stdin is valid\n")
        except json.JSONDecodeError as e:
            error_msg = f"Invalid JSON content from stdin: {e}"
            output_error(error_msg, verbose=verbose)
            raise RunError(error_msg, e) from e
        except Exception as e:
            error_msg = f"Error reading from stdin: {e}"
            output_error(error_msg, verbose=verbose)
            raise RunError(error_msg, e) from e

    try:
        if flow_dict is not None:
            if verbosity > 0:
                sys.stderr.write("Loading graph from JSON content...\n")
            from lfx.load import aload_flow_from_json

            graph = await aload_flow_from_json(flow_dict, disable_logs=not verbose)
        elif script_path is not None:
            if not script_path.exists():
                error_msg = f"File '{script_path}' does not exist."
                raise ValueError(error_msg)
            if not script_path.is_file():
                error_msg = f"'{script_path}' is not a file."
                raise ValueError(error_msg)
            file_extension = script_path.suffix.lower()
            if file_extension not in [".py", ".json"]:
                error_msg = f"'{script_path}' must be a .py or .json file."
                raise ValueError(error_msg)
            file_type = "Python script" if file_extension == ".py" else "JSON flow"
            if verbosity > 0:
                sys.stderr.write(f"Analyzing {file_type}: {script_path}\n")
            if file_extension == ".py":
                graph_info = find_graph_variable(script_path)
                if not graph_info:
                    error_msg = (
                        "No 'graph' variable found in the script. "
                        "Expected to find an assignment like: graph = Graph(...)"
                    )
                    raise ValueError(error_msg)
                if verbosity > 0:
                    sys.stderr.write(f"Found 'graph' variable at line {graph_info['line_number']}\n")
                    sys.stderr.write(f"Type: {graph_info['type']}\n")
                    sys.stderr.write(f"Source: {graph_info['source_line']}\n")
                    sys.stderr.write("Loading and executing script...\n")
                graph = await load_graph_from_script(script_path)
            else:  # .json 文件
                if verbosity > 0:
                    sys.stderr.write("Valid JSON flow file detected\n")
                    sys.stderr.write("Loading and executing JSON flow\n")
                from lfx.load import aload_flow_from_json

                graph = await aload_flow_from_json(script_path, disable_logs=not verbose)
        else:
            error_msg = "No input source provided"
            raise ValueError(error_msg)

        # 安全：全局变量可能包含敏感值；仅注入到上下文且只记录 key，不记录 value
        if global_variables:
            if "request_variables" not in graph.context:
                graph.context["request_variables"] = {}
            graph.context["request_variables"].update(global_variables)
            if verbosity > 0:
                # 仅记录 key，避免在日志中泄露敏感 value
                logger.info(f"Injected global variables: {list(global_variables.keys())}")

    except Exception as e:
        error_type = type(e).__name__
        logger.error(f"Graph loading failed with {error_type}")

        if verbosity > 0:
            # 排障：对常见导入/依赖问题给出更可操作的提示（否则用户只能看到一个 ImportError）
            if "ModuleNotFoundError" in str(e) or "No module named" in str(e):
                logger.info("This appears to be a missing dependency issue")
                if "langchain" in str(e).lower():
                    match = re.search(r"langchain_(.*)", str(e).lower())
                    if match:
                        module_name = match.group(1)
                        logger.info(
                            f"Missing LangChain dependency detected. Try: pip install langchain-{module_name}",
                        )
            elif "ImportError" in str(e):
                logger.info("This appears to be an import issue - check component dependencies")
            elif "AttributeError" in str(e):
                logger.info("This appears to be a component configuration issue")

            # 排障：verbose 模式下输出 traceback，便于定位根因（脚本导入副作用/依赖缺失等）
            logger.exception("Failed to load graph.")

        error_msg = f"Failed to load graph. {e}"
        output_error(error_msg, verbose=verbose, exception=e)
        raise RunError(error_msg, e) from e

    inputs = InputValueRequest(input_value=final_input_value) if final_input_value else None

    # 若启用 timing，记录加载阶段结束时间
    load_end_time = time.time() if timing else None

    if verbosity > 0:
        sys.stderr.write("Preparing graph for execution...\n")
    try:
        # 排障：详细模式下输出图结构概览，便于定位"缺组件/连线错误/环境差异"
        if verbosity > 0:
            logger.debug(f"Graph contains {len(graph.vertices)} vertices")
            logger.debug(f"Graph contains {len(graph.edges)} edges")

            component_types = set()
            for vertex in graph.vertices:
                if hasattr(vertex, "display_name"):
                    component_types.add(vertex.display_name)
            logger.debug(f"Component types in graph: {', '.join(sorted(component_types))}")

        graph.prepare()
        logger.info("Graph preparation completed")

        # 注意：默认在执行前做变量兼容性校验，提前暴露配置问题
        if check_variables:
            logger.info("Validating global variables...")
            validation_errors = validate_global_variables_for_env(graph)
            if validation_errors:
                error_details = "Global variable validation failed: " + "; ".join(validation_errors)
                logger.info(f"Variable validation failed: {len(validation_errors)} errors")
                for error in validation_errors:
                    logger.debug(f"Validation error: {error}")
                output_error(error_details, verbose=verbose)
                raise RunError(error_details, None)
            logger.info("Global variable validation passed")
        else:
            logger.info("Global variable validation skipped")
    except RunError:
        raise
    except Exception as e:
        error_type = type(e).__name__
        logger.info(f"Graph preparation failed with {error_type}")

        if verbosity > 0:
            logger.debug(f"Preparation error: {e!s}")
            logger.exception("Failed to prepare graph - full traceback:")

        error_msg = f"Failed to prepare graph: {e}"
        output_error(error_msg, verbose=verbose, exception=e)
        raise RunError(error_msg, e) from e

    logger.info("Executing graph...")
    execution_start_time = time.time() if timing else None
    if verbose:
        logger.debug("Setting up execution environment")
        if inputs:
            logger.debug(f"Input provided: {inputs.input_value}")
        else:
            logger.debug("No input provided")

    captured_stdout = StringIO()
    captured_stderr = StringIO()
    original_stdout = sys.stdout
    original_stderr = sys.stderr

    # 注意：`timing=True` 会额外记录每步耗时与组件信息；用于粗粒度定位，不保证绝对精度
    component_timings = [] if timing else None
    execution_step_start = execution_start_time if timing else None
    result_count = 0

    try:
        sys.stdout = captured_stdout
        # 注意：-vvv 时不捕获 stderr，避免与 logger 直出重复/错序
        if verbosity < VERBOSITY_FULL:
            sys.stderr = captured_stderr
        results = []

        logger.info("Starting graph execution...", level="DEBUG")

        # 实现：`graph.async_start(inputs)` 为异步迭代器，会逐步产出执行结果事件。
        # 注意：收集 `results` 便于输出聚合与异常统计，但长流程会占用更多内存。
        async for result in graph.async_start(inputs):
            result_count += 1
            if verbosity > 0:
                logger.debug(f"Processing result #{result_count}")
                if hasattr(result, "vertex") and hasattr(result.vertex, "display_name"):
                    logger.debug(f"Component: {result.vertex.display_name}")
            if timing:
                step_end_time = time.time()
                step_duration = step_end_time - execution_step_start

                if hasattr(result, "vertex"):
                    component_name = getattr(result.vertex, "display_name", "Unknown")
                    component_id = getattr(result.vertex, "id", "Unknown")
                    component_timings.append(
                        {
                            "component": component_name,
                            "component_id": component_id,
                            "duration": step_duration,
                            "cumulative_time": step_end_time - execution_start_time,
                        }
                    )

                execution_step_start = step_end_time

            results.append(result)

        logger.info(f"Graph execution completed. Processed {result_count} results")

    except Exception as e:
        error_type = type(e).__name__
        logger.info(f"Graph execution failed with {error_type}")

        if verbosity >= VERBOSITY_DETAILED:  # 仅在 -vv 及以上输出更多细节
            logger.debug(f"Failed after processing {result_count} results")

        # 排障：仅在 -vvv 回放组件输出，并尽量去重/去时间戳，避免把噪声写入结构化输出
        if verbosity >= VERBOSITY_FULL:
            captured_content = captured_stdout.getvalue()
            if captured_content.strip():
                error_text = str(e)
                captured_lines = captured_content.strip().split("\n")

                unique_lines = [
                    line
                    for line in captured_lines
                    if not any(
                        error_part.strip() in line for error_part in error_text.split("\n") if error_part.strip()
                    )
                ]

                if unique_lines:
                    logger.info("Component output before error:", level="DEBUG")
                    for line in unique_lines:
                        if verbosity > 0:
                            clean_line = line
                            if "] " in line and line.startswith("2025-"):
                                parts = line.split("] ", 1)
                                if len(parts) > 1:
                                    clean_line = parts[1]
                            logger.debug(clean_line)

            # 排障：针对常见执行错误补充提示（降低排障成本）
            if "list can't be used in 'await' expression" in str(e):
                logger.info("This appears to be an async/await mismatch in a component")
                logger.info("Check that async methods are properly awaited")
            elif "AttributeError" in error_type and "NoneType" in str(e):
                logger.info("This appears to be a null reference error")
                logger.info("A component may be receiving unexpected None values")
            elif "ConnectionError" in str(e) or "TimeoutError" in str(e):
                logger.info("This appears to be a network connectivity issue")
                logger.info("Check API keys and network connectivity")

            logger.exception("Failed to execute graph - full traceback:")

        sys.stdout = original_stdout
        sys.stderr = original_stderr
        error_msg = f"Failed to execute graph: {e}"
        output_error(error_msg, verbose=verbosity > 0, exception=e)
        raise RunError(error_msg, e) from e
    finally:
        sys.stdout = original_stdout
        sys.stderr = original_stderr

    execution_end_time = time.time() if timing else None

    captured_logs = captured_stdout.getvalue() + captured_stderr.getvalue()

    # 排障：`timing` 作为轻量观测点，帮助快速定位"慢在加载/准备/执行/哪个组件"
    timing_metadata = None
    if timing:
        load_duration = load_end_time - start_time
        execution_duration = execution_end_time - execution_start_time
        total_duration = execution_end_time - start_time

        timing_metadata = {
            "load_time": round(load_duration, 3),
            "execution_time": round(execution_duration, 3),
            "total_time": round(total_duration, 3),
            "component_timings": [
                {
                    "component": ct["component"],
                    "component_id": ct["component_id"],
                    "duration": round(ct["duration"], 3),
                    "cumulative_time": round(ct["cumulative_time"], 3),
                }
                for ct in component_timings
            ],
        }

    # 实现：统一通过 `extract_structured_result(results)` 聚合结果，再按 `output_format` 映射输出。
    # 注意：聚合模式避免分叉实现；流式输出不在当前契约内。
    if output_format == "json":
        result_data = extract_structured_result(results)
        result_data["logs"] = captured_logs
        if timing_metadata:
            result_data["timing"] = timing_metadata
        return result_data
    if output_format in {"text", "message"}:
        result_data = extract_structured_result(results)
        output_text = result_data.get("result", result_data.get("text", ""))
        return {"output": str(output_text), "format": output_format}
    if output_format == "result":
        return {"output": extract_text_from_result(results), "format": "result"}
    # 默认兜底：回到结构化输出（兼容未知 output_format）
    result_data = extract_structured_result(results)
    result_data["logs"] = captured_logs
    if timing_metadata:
        result_data["timing"] = timing_metadata
    return result_data
