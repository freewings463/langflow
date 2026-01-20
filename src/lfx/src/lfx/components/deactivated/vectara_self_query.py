"""
模块名称：Vectara 自查询检索组件（已停用）

本模块提供基于 Vectara 向量存储的自查询检索器构建能力，主要用于通过 LLM 自动生成过滤条件。主要功能包括：
- 解析元数据字段描述并构建 `AttributeInfo`
- 通过 `SelfQueryRetriever.from_llm` 构建检索器

关键组件：
- `VectaraSelfQueryRetriverComponent`：自查询检索组件

设计背景：历史上用于增强 Vectara 检索的结构化查询能力。
注意事项：`metadata_field_info` 需为 JSON 字符串列表且包含 `name/description/type`。
"""

# mypy: disable-error-code="attr-defined"
import json

from langchain.chains.query_constructor.base import AttributeInfo
from langchain.retrievers.self_query.base import SelfQueryRetriever

from lfx.base.vectorstores.model import check_cached_vector_store
from lfx.custom.custom_component.custom_component import CustomComponent
from lfx.io import HandleInput, StrInput


class VectaraSelfQueryRetriverComponent(CustomComponent):
    """Vectara 自查询检索组件。

    契约：输入 `vectorstore`/`llm`/`document_content_description` 与元数据描述。
    失败语义：元数据格式错误抛 `ValueError`；依赖缺失抛 `ImportError`。
    副作用：无。
    """

    display_name: str = "Vectara Self Query Retriever"
    description: str = "Implementation of Vectara Self Query Retriever"
    name = "VectaraSelfQueryRetriver"
    icon = "Vectara"
    legacy = True

    inputs = [
        HandleInput(
            name="vectorstore",
            display_name="Vector Store",
            info="Input Vectara Vector Store",
        ),
        HandleInput(
            name="llm",
            display_name="LLM",
            info="For self query retriever",
        ),
        StrInput(
            name="document_content_description",
            display_name="Document Content Description",
            info="For self query retriever",
        ),
        StrInput(
            name="metadata_field_info",
            display_name="Metadata Field Info",
            info="Each metadata field info is a string in the form of key value pair dictionary containing "
            "additional search metadata.\n"
            'Example input: {"name":"speech","description":"what name of the speech","type":'
            '"string or list[string]"}.\n'
            "The keys should remain constant(name, description, type)",
        ),
    ]

    @check_cached_vector_store
    def build_vector_store(self):
        """构建 Vectara 自查询检索器。

        契约：根据元数据描述生成 `AttributeInfo` 并创建检索器。
        失败语义：JSON 解析失败或字段缺失抛 `ValueError`；依赖缺失抛 `ImportError`。
        副作用：无。

        关键路径（三步）：
        1) 校验依赖并导入 Vectara 向量存储
        2) 解析 `metadata_field_info` 为 `AttributeInfo`
        3) 调用 `SelfQueryRetriever.from_llm` 返回检索器
        """
        try:
            from langchain_community.vectorstores import Vectara  # noqa: F401
        except ImportError as e:
            msg = "Could not import Vectara. Please install it with `pip install langchain-community`."
            raise ImportError(msg) from e

        metadata_field_obj = []
        for meta in self.metadata_field_info:
            meta_obj = json.loads(meta)
            if "name" not in meta_obj or "description" not in meta_obj or "type" not in meta_obj:
                msg = "Incorrect metadata field info format."
                raise ValueError(msg)
            attribute_info = AttributeInfo(
                name=meta_obj["name"],
                description=meta_obj["description"],
                type=meta_obj["type"],
            )
            metadata_field_obj.append(attribute_info)

        return SelfQueryRetriever.from_llm(
            self.llm,  # type: ignore[attr-defined]
            self.vectorstore,  # type: ignore[attr-defined]
            self.document_content_description,  # type: ignore[attr-defined]
            metadata_field_obj,
            verbose=True,
        )
