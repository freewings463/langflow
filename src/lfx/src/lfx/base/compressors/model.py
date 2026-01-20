"""
模块名称：压缩器组件基类

本模块提供面向 LFX 组件体系的压缩器抽象，封装查询与结果压缩的统一入口。主要功能包括：
- 规范 `search_query`/`search_results` 输入与 `Data`、`DataFrame` 输出
- 通过 `build_compressor` 接入外部 `BaseDocumentCompressor`
- 提供异步压缩与组件状态更新

关键组件：
- `LCCompressorComponent`：组件基类，要求子类实现压缩器构建

设计背景：组件层需要统一接入不同压缩器实现，避免重复封装。
使用场景：在 LFX 组件中对检索结果进行压缩与格式化输出。
注意事项：`search_results` 仅处理 `Data` 实例，非 `Data` 会被忽略。
"""

from abc import abstractmethod

from lfx.custom.custom_component.component import Component
from lfx.field_typing import BaseDocumentCompressor
from lfx.io import DataInput, IntInput, MultilineInput
from lfx.schema.data import Data
from lfx.schema.dataframe import DataFrame
from lfx.template.field.base import Output


class LCCompressorComponent(Component):
    """压缩器组件抽象

    契约：输入 `search_query` 字符串、`search_results`（仅 `Data`）、`top_n`；输出 `list[Data]`/`DataFrame`；
    副作用更新 `self.status`；失败语义：`build_compressor` 未实现抛 `NotImplementedError`，压缩器异常透传。
    关键路径：1) 子类实现 `build_compressor` 2) `compress_documents` 压缩并转 `Data`
    3) `compressed_documents_as_dataframe` 可选封装为 `DataFrame`。
    决策：以抽象方法强制子类注入压缩器实例。
    问题：组件层需要统一接入不同 `BaseDocumentCompressor` 实现。
    方案：定义 `build_compressor` 作为唯一扩展点。
    代价：子类实现负担增加，未实现时报错。
    重评：当压缩器创建可配置化且无需子类定制时。
    """

    inputs = [
        MultilineInput(
            name="search_query",
            display_name="Search Query",
            tool_mode=True,
        ),
        DataInput(
            name="search_results",
            display_name="Search Results",
            info="Search Results from a Vector Store.",
            is_list=True,
        ),
        IntInput(name="top_n", display_name="Top N", value=3, advanced=True),
    ]

    outputs = [
        Output(
            display_name="Data",
            name="compressed_documents",
            method="Compressed Documents",
        ),
        Output(
            display_name="DataFrame",
            name="compressed_documents_as_dataframe",
            method="Compressed Documents as DataFrame",
        ),
    ]

    @abstractmethod
    def build_compressor(self) -> BaseDocumentCompressor:
        """构建压缩器实例（由子类实现）

        契约：返回 `BaseDocumentCompressor` 实例；副作用无；
        失败语义：未实现抛 `NotImplementedError`。
        关键路径：子类创建并返回具体压缩器。
        决策：不提供默认实现。
        问题：不同压缩器需要不同构造参数。
        方案：要求子类实现构建逻辑。
        代价：缺省不可用，调用前需确保实现。
        重评：当存在统一工厂与配置方案时。
        """
        msg = "build_compressor method must be implemented."
        raise NotImplementedError(msg)

    async def compress_documents(self) -> list[Data]:
        """压缩检索结果并返回 `Data` 列表

        契约：使用实例字段 `search_query`/`search_results`；仅处理 `Data` 并调用 `to_lc_document`；
        输出 `list[Data]`；副作用：更新 `self.status`；
        失败语义：压缩器或 `to_data` 抛出的异常原样上抛。
        关键路径：1) `build_compressor` 获取压缩器 2) 调用 `compress_documents` 3) `to_data` 转换并写入状态。
        决策：在组件层过滤非 `Data` 结果。
        问题：搜索结果可能混入非 `Data` 类型。
        方案：仅对 `Data` 调用 `to_lc_document`。
        代价：非 `Data` 结果被静默丢弃。
        重评：当上游保证类型一致或需显式报错时。
        """
        compressor = self.build_compressor()
        documents = compressor.compress_documents(
            query=self.search_query,
            documents=[passage.to_lc_document() for passage in self.search_results if isinstance(passage, Data)],
        )
        data = self.to_data(documents)
        self.status = data
        return data

    async def compress_documents_as_dataframe(self) -> DataFrame:
        """将压缩结果包装为 `DataFrame`

        契约：调用 `compress_documents` 获取 `list[Data]` 并返回 `DataFrame`；
        副作用：继承 `compress_documents` 的状态更新；
        失败语义：下游异常透传。
        关键路径：1) await `compress_documents` 2) 构造 `DataFrame`。
        决策：在组件层返回 `DataFrame` 而非由调用方转换。
        问题：下游节点常使用表格结构。
        方案：提供专用输出方法减少重复转换。
        代价：引入额外对象分配。
        重评：当调用方全部使用 `Data` 或改用流式接口时。
        """
        data_objs = await self.compress_documents()
        return DataFrame(data=data_objs)
