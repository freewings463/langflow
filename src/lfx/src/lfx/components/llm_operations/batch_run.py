"""
模块名称：批量 `LLM` 运行组件

本模块提供对 `DataFrame` 按行调用模型的批处理能力，适用于批量标注、清洗与抽取。主要功能包括：
- 按列名或整行 `TOML` 格式生成输入
- 异步批量调用模型并保持输入顺序
- 可选写入元数据与失败行占位输出

关键组件：
- `BatchRunComponent.run_batch`：核心批处理与容错
- `_format_row_as_toml`/`_add_metadata`：输入与输出整形

设计背景：单条推理成本高且缺少批量可观测性，需要统一批处理入口。
注意事项：`api_key` 为空时除 `Ollama` 外将直接抛错。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

import toml  # type: ignore[import-untyped]

from lfx.base.models.unified_models import (
    get_language_model_options,
    get_model_classes,
    update_model_options_in_build_config,
)
from lfx.custom.custom_component.component import Component
from lfx.io import BoolInput, DataFrameInput, MessageTextInput, ModelInput, MultilineInput, Output, SecretStrInput
from lfx.log.logger import logger
from lfx.schema.dataframe import DataFrame

if TYPE_CHECKING:
    from langchain_core.runnables import Runnable


class BatchRunComponent(Component):
    """批量执行 `LLM` 的组件入口。

    契约：输入 `df` 与 `model`，输出包含 `output_column_name` 的新 `DataFrame`。
    决策：使用 `abatch` 批量调用并按索引排序恢复顺序。
    问题：逐条调用吞吐低且难以保证批量顺序一致。
    方案：构造对话列表后统一 `abatch`，再按索引排序。
    代价：对话列表一次性常驻内存，行数过大时占用升高。
    重评：当单批行数 > 5000 或内存告警频发时拆分批次。
    """
    display_name = "Batch Run"
    description = "Runs an LLM on each row of a DataFrame column. If no column is specified, all columns are used."
    documentation: str = "https://docs.langflow.org/batch-run"
    icon = "List"

    inputs = [
        ModelInput(
            name="model",
            display_name="Language Model",
            info="Select your model provider",
            real_time_refresh=True,
            required=True,
        ),
        SecretStrInput(
            name="api_key",
            display_name="API Key",
            info="Model Provider API key",
            real_time_refresh=True,
            advanced=True,
        ),
        MultilineInput(
            name="system_message",
            display_name="Instructions",
            info="Multi-line system instruction for all rows in the DataFrame.",
            required=False,
        ),
        DataFrameInput(
            name="df",
            display_name="DataFrame",
            info="The DataFrame whose column (specified by 'column_name') we'll treat as text messages.",
            required=True,
        ),
        MessageTextInput(
            name="column_name",
            display_name="Column Name",
            info=(
                "The name of the DataFrame column to treat as text messages. "
                "If empty, all columns will be formatted in TOML."
            ),
            required=False,
            advanced=False,
        ),
        MessageTextInput(
            name="output_column_name",
            display_name="Output Column Name",
            info="Name of the column where the model's response will be stored.",
            value="model_response",
            required=False,
            advanced=True,
        ),
        BoolInput(
            name="enable_metadata",
            display_name="Enable Metadata",
            info="If True, add metadata to the output DataFrame.",
            value=False,
            required=False,
            advanced=True,
        ),
    ]

    outputs = [
        Output(
            display_name="LLM Results",
            name="batch_results",
            method="run_batch",
            info="A DataFrame with all original columns plus the model's response column.",
        ),
    ]

    def update_build_config(self, build_config: dict, field_value: str, field_name: str | None = None):
        """根据用户筛选刷新模型选项缓存。

        契约：输入当前配置与字段变化，返回更新后的构建配置。
        失败语义：由上游配置处理函数抛出异常。
        """
        return update_model_options_in_build_config(
            component=self,
            build_config=build_config,
            cache_key_prefix="language_model_options",
            get_options_func=get_language_model_options,
            field_name=field_name,
            field_value=field_value,
        )

    def _format_row_as_toml(self, row: dict[str, Any]) -> str:
        """将行数据转为 `TOML` 文本。

        契约：所有值会被强制转为字符串并包在 `value` 字段下。
        注意：该格式用于稳定提示上下文，不保留原始类型。
        """
        formatted_dict = {str(col): {"value": str(val)} for col, val in row.items()}
        return toml.dumps(formatted_dict)

    def _create_base_row(
        self, original_row: dict[str, Any], model_response: str = "", batch_index: int = -1
    ) -> dict[str, Any]:
        """构建包含模型结果与索引的输出行。

        契约：保留原列，追加 `output_column_name` 与 `batch_index`。
        """
        row = original_row.copy()
        row[self.output_column_name] = model_response
        row["batch_index"] = batch_index
        return row

    def _add_metadata(
        self, row: dict[str, Any], *, success: bool = True, system_msg: str = "", error: str | None = None
    ) -> None:
        """按需为输出行追加元数据。

        契约：仅当 `enable_metadata=True` 时写入 `metadata` 字段。
        失败语义：失败分支仅记录 `error` 与 `processing_status`。
        """
        if not self.enable_metadata:
            return

        if success:
            row["metadata"] = {
                "has_system_message": bool(system_msg),
                "input_length": len(row.get("text_input", "")),
                "response_length": len(row[self.output_column_name]),
                "processing_status": "success",
            }
        else:
            row["metadata"] = {
                "error": error,
                "processing_status": "failed",
            }

    async def run_batch(self) -> DataFrame:
        """批量调用模型并返回结构化结果。

        契约：输入 `df` 与可选 `column_name`，输出带 `batch_index` 的 `DataFrame`。
        关键路径（三步）：
        1) 校验输入与模型配置
        2) 生成对话列表并执行 `abatch`
        3) 组装结果与可选元数据
        异常流：`TypeError`(非 `DataFrame`)、`ValueError`(列缺失或缺少 `api_key`)。
        性能瓶颈：大批量 `abatch` 与 `TOML` 序列化。
        排障入口：日志关键字 `Processing`/`Batch processing`/`Data processing error`。
        """
        # 注意：测试场景可直接传入模型实例，否则按配置构建。
        if isinstance(self.model, list):
            # 实现：从配置中解析模型名称/提供方/元数据。
            model_selection = self.model[0]
            model_name = model_selection.get("name")
            provider = model_selection.get("provider")
            metadata = model_selection.get("metadata", {})

            # 注意：模型类来自元数据映射，缺失时直接失败。
            model_class = get_model_classes().get(metadata.get("model_class"))
            if model_class is None:
                msg = f"No model class defined for {model_name}"
                raise ValueError(msg)

            api_key_param = metadata.get("api_key_param", "api_key")
            model_name_param = metadata.get("model_name_param", "model")

            # 注意：优先读取全局配置的 `api_key`。
            from lfx.base.models.unified_models import get_api_key_for_provider

            api_key = get_api_key_for_provider(self.user_id, provider, self.api_key)

            if not api_key and provider != "Ollama":
                msg = f"{provider} API key is required. Please configure it globally."
                raise ValueError(msg)

            # 实现：按模型参数实例化。
            kwargs = {
                model_name_param: model_name,
                api_key_param: api_key,
            }
            model: Runnable = model_class(**kwargs)
        else:
            # 注意：测试或上游已构造模型实例，直接复用。
            model = self.model

        system_msg = self.system_message or ""
        df: DataFrame = self.df
        col_name = self.column_name or ""

        # 注意：先校验输入类型与列名，避免隐式失败。
        if not isinstance(df, DataFrame):
            msg = f"Expected DataFrame input, got {type(df)}"
            raise TypeError(msg)

        if col_name and col_name not in df.columns:
            msg = f"Column '{col_name}' not found in the DataFrame. Available columns: {', '.join(df.columns)}"
            raise ValueError(msg)

        try:
            # 实现：根据列名或整行 `TOML` 生成文本输入。
            if col_name:
                user_texts = df[col_name].astype(str).tolist()
            else:
                user_texts = [
                    self._format_row_as_toml(cast("dict[str, Any]", row)) for row in df.to_dict(orient="records")
                ]

            total_rows = len(user_texts)
            await logger.ainfo(f"Processing {total_rows} rows with batch run")

            # 实现：构造对话批次，必要时附加 `system` 指令。
            conversations = [
                [{"role": "system", "content": system_msg}, {"role": "user", "content": text}]
                if system_msg
                else [{"role": "user", "content": text}]
                for text in user_texts
            ]

            # 注意：部分模型在 `with_config` 中会因 `SecretStr` 等不可序列化字段失败。
            try:
                model = model.with_config(
                    {
                        "run_name": self.display_name,
                        "project_name": self.get_project_name(),
                        "callbacks": self.get_langchain_callbacks(),
                    }
                )
            except (TypeError, ValueError, AttributeError) as e:
                # 排障：记录降级信息，继续无配置执行。
                await logger.awarning(
                    f"Could not configure model with callbacks and project info: {e!s}. "
                    "Proceeding with batch processing without configuration."
                )
            # 实现：批量执行并记录索引，后续按序恢复输出。
            responses_with_idx = list(
                zip(
                    range(len(conversations)),
                    await model.abatch(list(conversations)),
                    strict=True,
                )
            )

            # 注意：模型返回顺序不保证，需按索引稳定排序。
            responses_with_idx.sort(key=lambda x: x[0])

            # 实现：组装结果行并追加元数据。
            rows: list[dict[str, Any]] = []
            for idx, (original_row, response) in enumerate(
                zip(df.to_dict(orient="records"), responses_with_idx, strict=False)
            ):
                response_text = response[1].content if hasattr(response[1], "content") else str(response[1])
                row = self._create_base_row(
                    cast("dict[str, Any]", original_row), model_response=response_text, batch_index=idx
                )
                self._add_metadata(row, success=True, system_msg=system_msg)
                rows.append(row)

                # 排障：按 10% 进度打点，便于长批次观测。
                if (idx + 1) % max(1, total_rows // 10) == 0:
                    await logger.ainfo(f"Processed {idx + 1}/{total_rows} rows")

            await logger.ainfo("Batch processing completed successfully")
            return DataFrame(rows)

        except (KeyError, AttributeError) as e:
            # 排障：结构异常时返回失败占位行。
            await logger.aerror(f"Data processing error: {e!s}")
            error_row = self._create_base_row(dict.fromkeys(df.columns, ""), model_response="", batch_index=-1)
            self._add_metadata(error_row, success=False, error=str(e))
            return DataFrame([error_row])
