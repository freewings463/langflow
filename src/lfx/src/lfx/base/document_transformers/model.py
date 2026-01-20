"""
模块名称：文档转换器组件基类

本模块提供面向 LFX 组件体系的文档转换器抽象，主要用于将 `Data`/文档输入交给 LangChain 的
`BaseDocumentTransformer` 处理后再回写为 `Data`。
主要功能包括：
- 统一输入形态（单个/列表）并转换为 LangChain 文档
- 调用自定义 transformer 完成文档变换
- 将结果还原为 `Data` 并生成组件展示用的 `repr_value`

关键组件：
- `LCDocumentTransformerComponent`：文档转换组件基类

设计背景：在 LFX 组件系统中复用 LangChain 的文档变换能力，同时保持 `Data` 作为统一数据载体。
注意事项：子类必须实现输入获取与 transformer 构建，否则运行时会触发抽象方法错误。
"""

from abc import abstractmethod
from typing import Any

from langchain_core.documents import BaseDocumentTransformer

from lfx.custom.custom_component.component import Component
from lfx.io import Output
from lfx.schema.data import Data
from lfx.utils.util import build_loader_repr_from_data


class LCDocumentTransformerComponent(Component):
    """面向 LFX 的文档转换组件基类。

    契约：`get_data_input()` 返回 `Data`/文档或其列表；`transform_data()` 输出 `list[Data]`。
    副作用：更新 `self.repr_value` 供前端展示。
    失败语义：下游 transformer 或 `to_data()` 的异常不在此处捕获，交由上层处理。
    决策：通过 LangChain `BaseDocumentTransformer` 适配文档变换
    问题：组件系统需要复用成熟的文档变换生态而非自建
    方案：要求子类提供 `build_document_transformer()`，在基类中统一调用与数据转换
    代价：强依赖 LangChain 文档格式，子类需额外适配
    重评：当内部文档协议完全脱离 LangChain 时评估移除该依赖
    """

    trace_type = "document_transformer"
    outputs = [
        Output(display_name="Data", name="data", method="transform_data"),
    ]

    def transform_data(self) -> list[Data]:
        """将输入文档变换为 `Data` 列表并生成展示摘要；输入来自 `get_data_input()`，支持
        `Data`/文档/其列表，输出 `list[Data]` 且更新 `self.repr_value`。
        失败语义：`transform_documents` 或 `to_data` 的异常不捕获，交由上层处理。
        关键路径（三步）：
        1) 规范化输入为列表并将 `Data` 转为文档
        2) 调用 `build_document_transformer().transform_documents`
        3) 转回 `Data` 并生成 `repr_value`
        异常流：输入对象不可转换为文档或 transformer 内部异常会直接中断执行。
        性能瓶颈：主要耗时由 `transform_documents` 决定，随文档规模与实现差异变化。
        排障入口：比对 `documents` 构造数量与 `repr_value` 是否一致。
        决策：允许单个或列表输入并统一为列表
        问题：上游输出形态不一致导致调用方负担
        方案：入口处封装为列表并逐项转换 `Data`
        代价：增加一次类型判断与列表包装
        重评：当上游统一输出列表且无兼容负担时可移除
        """

        data_input = self.get_data_input()
        documents = []

        if not isinstance(data_input, list):
            data_input = [data_input]

        for _input in data_input:
            if isinstance(_input, Data):
                documents.append(_input.to_lc_document())
            else:
                documents.append(_input)

        transformer = self.build_document_transformer()
        docs = transformer.transform_documents(documents)
        data = self.to_data(docs)
        self.repr_value = build_loader_repr_from_data(data)
        return data

    @abstractmethod
    def get_data_input(self) -> Any:
        """获取上游输入对象供文档变换使用。

        契约：返回 `Data`/文档或其列表；不得返回 `None`。
        关键路径：读取上游输入并完成必要的类型校验后返回。
        失败语义：输入解析错误应在子类中抛出明确异常。
        决策：由子类负责输入获取以避免基类依赖具体上游协议
        问题：不同组件的输入来源与校验规则差异大
        方案：将输入定义为抽象方法由子类实现
        代价：子类需重复实现校验逻辑
        重评：当输入协议稳定且可复用时考虑提供默认实现
        """

    @abstractmethod
    def build_document_transformer(self) -> BaseDocumentTransformer:
        """构建用于文档变换的 LangChain transformer 实例。

        契约：返回 `BaseDocumentTransformer`，应是可重入/无状态或自行处理并发。
        关键路径：读取配置 → 初始化 transformer → 返回实例。
        失败语义：初始化失败需抛出具体异常，供调用方记录。
        决策：在基类只调用接口，不约束具体实现细节
        问题：不同 transformer 需要不同配置与依赖
        方案：由子类创建并返回具体实现
        代价：基类无法提前验证配置正确性
        重评：当配置趋于统一时考虑引入工厂
        """
