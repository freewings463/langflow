"""
模块名称：assemblyai_list_transcripts

本模块提供 AssemblyAI 转写列表组件，支持条件筛选与分页。
主要功能包括：
- 构造列表查询参数并请求 API
- 将转写结果转换为 `Data` 列表

关键组件：
- `AssemblyAIListTranscripts`：转写列表组件

设计背景：需要在流程内查看并筛选历史转写任务
使用场景：批量管理或审计转写记录
注意事项：`limit=0` 表示拉取全部分页
"""

import assemblyai as aai

from lfx.custom.custom_component.component import Component
from lfx.io import BoolInput, DropdownInput, IntInput, MessageTextInput, Output, SecretStrInput
from lfx.log.logger import logger
from lfx.schema.data import Data


class AssemblyAIListTranscripts(Component):
    """AssemblyAI 转写列表组件。

    契约：提供 `api_key`，可选 `status_filter/created_on` 等条件。
    副作用：调用 AssemblyAI API 并写入 `status`。
    失败语义：异常捕获后返回包含 `error` 的 `Data` 列表。
    排障入口：日志 `Error listing transcripts` 与 `status`。
    """
    display_name = "AssemblyAI List Transcripts"
    description = "Retrieve a list of transcripts from AssemblyAI with filtering options"
    documentation = "https://www.assemblyai.com/docs"
    icon = "AssemblyAI"

    inputs = [
        SecretStrInput(
            name="api_key",
            display_name="Assembly API Key",
            info="Your AssemblyAI API key. You can get one from https://www.assemblyai.com/",
            required=True,
        ),
        IntInput(
            name="limit",
            display_name="Limit",
            info="Maximum number of transcripts to retrieve (default: 20, use 0 for all)",
            value=20,
        ),
        DropdownInput(
            name="status_filter",
            display_name="Status Filter",
            options=["all", "queued", "processing", "completed", "error"],
            value="all",
            info="Filter by transcript status",
            advanced=True,
        ),
        MessageTextInput(
            name="created_on",
            display_name="Created On",
            info="Only get transcripts created on this date (YYYY-MM-DD)",
            advanced=True,
        ),
        BoolInput(
            name="throttled_only",
            display_name="Throttled Only",
            info="Only get throttled transcripts, overrides the status filter",
            advanced=True,
        ),
    ]

    outputs = [
        Output(display_name="Transcript List", name="transcript_list", method="list_transcripts"),
    ]

    def list_transcripts(self) -> list[Data]:
        """获取转写列表并返回 `Data` 列表。

        契约：`limit=0` 代表拉取全部；其余为单页结果。
        副作用：设置 `aai.settings.api_key` 并进行网络请求。
        失败语义：异常时返回仅含 `error` 的列表。
        关键路径（三步）：1) 构造参数 2) 请求分页 3) 转为 `Data`。
        决策：`limit=0` 触发全量分页拉取。
        问题：列表查询默认分页，无法一次取全量。
        方案：用 `before_id` 迭代分页直至为空。
        代价：请求次数增加，耗时与配额上升。
        重评：当 API 支持服务端聚合或导出接口时。
        """
        aai.settings.api_key = self.api_key

        params = aai.ListTranscriptParameters()
        if self.limit:
            params.limit = self.limit
        if self.status_filter != "all":
            params.status = self.status_filter
        if self.created_on and self.created_on.text:
            params.created_on = self.created_on.text
        if self.throttled_only:
            params.throttled_only = True

        try:
            transcriber = aai.Transcriber()

            def convert_page_to_data_list(page):
                return [Data(**t.dict()) for t in page.transcripts]

            if self.limit == 0:
                # 注意：全量拉取会循环分页直到无前一页。
                params.limit = 100
                page = transcriber.list_transcripts(params)
                transcripts = convert_page_to_data_list(page)

                while page.page_details.before_id_of_prev_url is not None:
                    params.before_id = page.page_details.before_id_of_prev_url
                    page = transcriber.list_transcripts(params)
                    transcripts.extend(convert_page_to_data_list(page))
            else:
                # 注意：非全量场景只取单页。
                page = transcriber.list_transcripts(params)
                transcripts = convert_page_to_data_list(page)

        except Exception as e:  # noqa: BLE001
            logger.debug("Error listing transcripts", exc_info=True)
            error_data = Data(data={"error": f"An error occurred: {e}"})
            self.status = [error_data]
            return [error_data]

        self.status = transcripts
        return transcripts
