"""
模块名称：流程输出数据转换工具

本模块提供运行结果到 `Data` 的转换与输出格式化工具，主要用于将图运行的结果结构
统一为上层可展示的数据列表或字符串。
主要功能包括：
- 从 `RunOutputs` 聚合构建 `Data` 列表
- 从 `ResultData` 解析消息/结果/制品
- 将 `Data` 列表格式化为文本输出

关键组件：
- `build_data_from_run_outputs`
- `build_data_from_result_data`
- `format_flow_output_data`

设计背景：运行结果结构存在多种形态（消息、字典、列表等），需要统一出口。
注意事项：未知 `artifact` 类型会记录警告日志，调用方需关注日志排障。
"""

from lfx.graph.schema import ResultData, RunOutputs
from lfx.log.logger import logger
from lfx.schema.data import Data
from lfx.schema.message import Message


def build_data_from_run_outputs(run_outputs: RunOutputs) -> list[Data]:
    """从 `RunOutputs` 构建 `Data` 列表

    契约：
    - 输入：`RunOutputs` 运行结果对象
    - 输出：`Data` 列表（可能为空）
    - 副作用：无
    - 失败语义：`run_outputs` 为空时返回空列表
    """
    if not run_outputs:
        return []
    data = []
    for result_data in run_outputs.outputs:
        if result_data:
            data.extend(build_data_from_result_data(result_data))
    return data


def build_data_from_result_data(result_data: ResultData) -> list[Data]:
    """从 `ResultData` 构建 `Data` 列表

    关键路径（三步）：
    1) 读取 `messages` 与 `artifacts` 的类型
    2) 解析结果为 `Data` 或 `Message`
    3) 返回结构化 `Data` 列表

    异常流：未知 `artifact` 类型会记录警告并跳过。
    性能瓶颈：大量 `artifacts`/`results` 迭代时。
    排障入口：日志关键字 "Unable to build record output from unknown ResultData.artifact"。
    
    契约：
    - 输入：`ResultData` 结果对象
    - 输出：`Data` 列表（可能为空）
    - 副作用：可能记录警告日志
    - 失败语义：无法解析时返回空列表
    """
    messages = result_data.messages

    if not messages:
        return []
    data = []

    # 注意：无聊天消息时按 `calling flow` 结果处理
    if not messages:
        # 注意：`artifact` 为单条记录
        if isinstance(result_data.artifacts, dict):
            data.append(Data(data=result_data.artifacts))
        # 注意：`artifact` 为列表
        elif isinstance(result_data.artifacts, list):
            for artifact in result_data.artifacts:
                # 注意：若 `artifact` 已是 `Data`，直接透传
                if isinstance(artifact, Data):
                    data.append(artifact)
                else:
                    # 注意：未知 `artifact` 类型时记录警告
                    logger.warning(f"Unable to build record output from unknown ResultData.artifact: {artifact}")
        # 注意：聊天或文本结果走 `results` 分支
        elif result_data.results:
            data.append(Data(data={"result": result_data.results}, text_key="result"))
            return data
        else:
            return []

    if isinstance(result_data.results, dict):
        for name, result in result_data.results.items():
            dataobj: Data | Message | None
            dataobj = result if isinstance(result, Message) else Data(data=result, text_key=name)

            data.append(dataobj)
    else:
        data.append(Data(data=result_data.results))
    return data


def format_flow_output_data(data: list[Data]) -> str:
    """将流程输出数据格式化为字符串

    契约：
    - 输入：`Data` 列表
    - 输出：格式化字符串
    - 副作用：无
    - 失败语义：空列表时仅返回标题行
    """
    result = "Flow run output:\n"
    results = "\n".join([value.get_text() if hasattr(value, "get_text") else str(value) for value in data])
    return result + results
