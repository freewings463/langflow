"""
模块名称：export_docling_document

本模块提供 DoclingDocument 导出组件，支持 Markdown/HTML/文本等格式。
主要功能包括：
- 根据选择的格式导出文档内容
- 将导出结果转为 `Data` 或 `DataFrame`

关键组件：
- `ExportDoclingDocumentComponent`：文档导出组件

设计背景：文档解析后需要以多格式输出供下游消费
使用场景：将 DoclingDocument 导出为 Markdown/HTML 等
注意事项：图像导出模式在不同格式下可用性不同
"""

from typing import Any

from docling_core.types.doc import ImageRefMode

from lfx.base.data.docling_utils import extract_docling_documents
from lfx.custom import Component
from lfx.io import DropdownInput, HandleInput, MessageTextInput, Output, StrInput
from lfx.schema import Data, DataFrame


class ExportDoclingDocumentComponent(Component):
    """DoclingDocument 导出组件。

    契约：输入包含 `doc_key` 的 `Data`/`DataFrame`；输出对应格式文本。
    副作用：可能更新 `status` 提示警告。
    失败语义：导出失败抛 `TypeError`。
    决策：在同一组件内支持多种导出格式。
    问题：不同下游需要不同格式（Markdown/HTML/文本）。
    方案：通过 `export_format` 参数统一切换导出路径。
    代价：配置复杂度提升且分支逻辑增多。
    重评：当格式差异过大需要拆分组件时。
    """
    display_name: str = "Export DoclingDocument"
    description: str = "Export DoclingDocument to markdown, html or other formats."
    documentation = "https://docling-project.github.io/docling/"
    icon = "Docling"
    name = "ExportDoclingDocument"

    inputs = [
        HandleInput(
            name="data_inputs",
            display_name="Data or DataFrame",
            info="The data with documents to export.",
            input_types=["Data", "DataFrame"],
            required=True,
        ),
        DropdownInput(
            name="export_format",
            display_name="Export format",
            options=["Markdown", "HTML", "Plaintext", "DocTags"],
            info="Select the export format to convert the input.",
            value="Markdown",
            real_time_refresh=True,
        ),
        DropdownInput(
            name="image_mode",
            display_name="Image export mode",
            options=["placeholder", "embedded"],
            info=(
                "Specify how images are exported in the output. Placeholder will replace the images with a string, "
                "whereas Embedded will include them as base64 encoded images."
            ),
            value="placeholder",
        ),
        StrInput(
            name="md_image_placeholder",
            display_name="Image placeholder",
            info="Specify the image placeholder for markdown exports.",
            value="<!-- image -->",
            advanced=True,
        ),
        StrInput(
            name="md_page_break_placeholder",
            display_name="Page break placeholder",
            info="Add this placeholder betweek pages in the markdown output.",
            value="",
            advanced=True,
        ),
        MessageTextInput(
            name="doc_key",
            display_name="Doc Key",
            info="The key to use for the DoclingDocument column.",
            value="doc",
            advanced=True,
        ),
    ]

    outputs = [
        Output(display_name="Exported data", name="data", method="export_document"),
        Output(display_name="DataFrame", name="dataframe", method="as_dataframe"),
    ]

    def update_build_config(self, build_config: dict, field_value: Any, field_name: str | None = None) -> dict:
        """根据导出格式显示/隐藏相关参数。

        契约：仅修改 `build_config` 的展示状态。
        副作用：更新输入项的 `show` 标志。
        失败语义：无显式异常。
        关键路径（三步）：1) 判断导出格式 2) 切换显示项 3) 返回配置。
        性能瓶颈：无显著性能开销。
        决策：Markdown/HTML 允许图像模式；文本/DocTags 禁止图像参数。
        问题：不相关参数会误导用户配置。
        方案：按导出格式动态显示。
        代价：配置逻辑需要随格式扩展同步维护。
        重评：当 UI 支持格式自描述能力时。
        """
        if field_name == "export_format" and field_value == "Markdown":
            build_config["md_image_placeholder"]["show"] = True
            build_config["md_page_break_placeholder"]["show"] = True
            build_config["image_mode"]["show"] = True
        elif field_name == "export_format" and field_value == "HTML":
            build_config["md_image_placeholder"]["show"] = False
            build_config["md_page_break_placeholder"]["show"] = False
            build_config["image_mode"]["show"] = True
        elif field_name == "export_format" and field_value in {"Plaintext", "DocTags"}:
            build_config["md_image_placeholder"]["show"] = False
            build_config["md_page_break_placeholder"]["show"] = False
            build_config["image_mode"]["show"] = False

        return build_config

    def export_document(self) -> list[Data]:
        """导出 DoclingDocument 并返回 `Data` 列表。

        契约：`data_inputs` 中需包含 `doc_key` 指定列。
        副作用：可能写入 `status` 警告。
        失败语义：导出失败抛 `TypeError`。
        关键路径（三步）：1) 提取文档 2) 选择导出格式 3) 组装结果。
        异常流：文档导出内部异常。
        性能瓶颈：导出过程受文档体积与图像处理影响。
        决策：在导出时按格式选择不同的 Docling 导出接口。
        问题：不同格式需要不同导出方法与参数。
        方案：基于 `export_format` 进行分支调用。
        代价：维护多个导出路径与参数组合。
        重评：当 Docling 提供统一导出接口时。
        """
        documents, warning = extract_docling_documents(self.data_inputs, self.doc_key)
        if warning:
            self.status = warning

        results: list[Data] = []
        try:
            image_mode = ImageRefMode(self.image_mode)
            for doc in documents:
                content = ""
                if self.export_format == "Markdown":
                    content = doc.export_to_markdown(
                        image_mode=image_mode,
                        image_placeholder=self.md_image_placeholder,
                        page_break_placeholder=self.md_page_break_placeholder,
                    )
                elif self.export_format == "HTML":
                    content = doc.export_to_html(image_mode=image_mode)
                elif self.export_format == "Plaintext":
                    content = doc.export_to_text()
                elif self.export_format == "DocTags":
                    content = doc.export_to_doctags()

                results.append(Data(text=content))
        except Exception as e:
            msg = f"Error splitting text: {e}"
            raise TypeError(msg) from e

        return results

    def as_dataframe(self) -> DataFrame:
        """将导出结果包装为 `DataFrame`。"""
        return DataFrame(self.export_document())
