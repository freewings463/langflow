"""
模块名称：`composio` 组件包入口

本模块提供 `lfx.components.composio` 的懒加载导出，集中管理 Composio 生态组件。
主要功能包括：
- 通过 `__getattr__` 动态导入组件，降低启动成本
- 统一对外导出列表，便于组件发现与注册

关键组件：
- ComposioAPIComponent：Composio 工具总入口
- 各 `Composio*APIComponent`：具体应用的工具封装

设计背景：Composio 组件数量多且依赖重，按需加载可减少导入开销。
注意事项：访问未注册的导出名会抛 `AttributeError`。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from lfx.components._importing import import_mod

if TYPE_CHECKING:
    from .agentql_composio import ComposioAgentQLAPIComponent
    from .agiled_composio import ComposioAgiledAPIComponent
    from .airtable_composio import ComposioAirtableAPIComponent
    from .apollo_composio import ComposioApolloAPIComponent
    from .asana_composio import ComposioAsanaAPIComponent
    from .attio_composio import ComposioAttioAPIComponent
    from .bitbucket_composio import ComposioBitbucketAPIComponent
    from .bolna_composio import ComposioBolnaAPIComponent
    from .brightdata_composio import ComposioBrightdataAPIComponent
    from .calendly_composio import ComposioCalendlyAPIComponent
    from .canva_composio import ComposioCanvaAPIComponent
    from .canvas_composio import ComposioCanvasAPIComponent
    from .coda_composio import ComposioCodaAPIComponent
    from .composio_api import ComposioAPIComponent
    from .contentful_composio import ComposioContentfulAPIComponent
    from .digicert_composio import ComposioDigicertAPIComponent
    from .discord_composio import ComposioDiscordAPIComponent
    from .elevenlabs_composio import ComposioElevenLabsAPIComponent
    from .exa_composio import ComposioExaAPIComponent
    from .figma_composio import ComposioFigmaAPIComponent
    from .finage_composio import ComposioFinageAPIComponent
    from .firecrawl_composio import ComposioFirecrawlAPIComponent
    from .fireflies_composio import ComposioFirefliesAPIComponent
    from .fixer_composio import ComposioFixerAPIComponent
    from .flexisign_composio import ComposioFlexisignAPIComponent
    from .freshdesk_composio import ComposioFreshdeskAPIComponent
    from .github_composio import ComposioGitHubAPIComponent
    from .gmail_composio import ComposioGmailAPIComponent
    from .googlebigquery_composio import ComposioGoogleBigQueryAPIComponent
    from .googlecalendar_composio import ComposioGoogleCalendarAPIComponent
    from .googleclassroom_composio import ComposioGoogleclassroomAPIComponent
    from .googledocs_composio import ComposioGoogleDocsAPIComponent
    from .googlemeet_composio import ComposioGooglemeetAPIComponent
    from .googlesheets_composio import ComposioGoogleSheetsAPIComponent
    from .googletasks_composio import ComposioGoogleTasksAPIComponent
    from .heygen_composio import ComposioHeygenAPIComponent
    from .instagram_composio import ComposioInstagramAPIComponent
    from .jira_composio import ComposioJiraAPIComponent
    from .jotform_composio import ComposioJotformAPIComponent
    from .klaviyo_composio import ComposioKlaviyoAPIComponent
    from .linear_composio import ComposioLinearAPIComponent
    from .listennotes_composio import ComposioListennotesAPIComponent
    from .mem0_composio import ComposioMem0APIComponent
    from .miro_composio import ComposioMiroAPIComponent
    from .missive_composio import ComposioMissiveAPIComponent
    from .notion_composio import ComposioNotionAPIComponent
    from .onedrive_composio import ComposioOneDriveAPIComponent
    from .outlook_composio import ComposioOutlookAPIComponent
    from .pandadoc_composio import ComposioPandadocAPIComponent
    from .peopledatalabs_composio import ComposioPeopleDataLabsAPIComponent
    from .perplexityai_composio import ComposioPerplexityAIAPIComponent
    from .reddit_composio import ComposioRedditAPIComponent
    from .serpapi_composio import ComposioSerpAPIComponent
    from .slack_composio import ComposioSlackAPIComponent
    from .slackbot_composio import ComposioSlackbotAPIComponent
    from .snowflake_composio import ComposioSnowflakeAPIComponent
    from .supabase_composio import ComposioSupabaseAPIComponent
    from .tavily_composio import ComposioTavilyAPIComponent
    from .timelinesai_composio import ComposioTimelinesAIAPIComponent
    from .todoist_composio import ComposioTodoistAPIComponent
    from .wrike_composio import ComposioWrikeAPIComponent
    from .youtube_composio import ComposioYoutubeAPIComponent


_dynamic_imports = {
    "ComposioAPIComponent": "composio_api",
    "ComposioCanvaAPIComponent": "canva_composio",
    "ComposioCodaAPIComponent": "coda_composio",
    "ComposioSlackAPIComponent": "slack_composio",
    "ComposioRedditAPIComponent": "reddit_composio",
    "ComposioSlackbotAPIComponent": "slackbot_composio",
    "ComposioPeopleDataLabsAPIComponent": "peopledatalabs_composio",
    "ComposioPerplexityAIAPIComponent": "perplexityai_composio",
    "ComposioSupabaseAPIComponent": "supabase_composio",
    "ComposioSerpAPIComponent": "serpapi_composio",
    "ComposioSnowflakeAPIComponent": "snowflake_composio",
    "ComposioTodoistAPIComponent": "todoist_composio",
    "ComposioTavilyAPIComponent": "tavily_composio",
    "ComposioYoutubeAPIComponent": "youtube_composio",
    "ComposioAgentQLAPIComponent": "agentql_composio",
    "ComposioAgiledAPIComponent": "agiled_composio",
    "ComposioAirtableAPIComponent": "airtable_composio",
    "ComposioApolloAPIComponent": "apollo_composio",
    "ComposioAsanaAPIComponent": "asana_composio",
    "ComposioAttioAPIComponent": "attio_composio",
    "ComposioBitbucketAPIComponent": "bitbucket_composio",
    "ComposioBolnaAPIComponent": "bolna_composio",
    "ComposioBrightdataAPIComponent": "brightdata_composio",
    "ComposioCalendlyAPIComponent": "calendly_composio",
    "ComposioCanvasAPIComponent": "canvas_composio",
    "ComposioContentfulAPIComponent": "contentful_composio",
    "ComposioDigicertAPIComponent": "digicert_composio",
    "ComposioDiscordAPIComponent": "discord_composio",
    "ComposioElevenLabsAPIComponent": "elevenlabs_composio",
    "ComposioExaAPIComponent": "exa_composio",
    "ComposioFigmaAPIComponent": "figma_composio",
    "ComposioFinageAPIComponent": "finage_composio",
    "ComposioFirecrawlAPIComponent": "firecrawl_composio",
    "ComposioFirefliesAPIComponent": "fireflies_composio",
    "ComposioFixerAPIComponent": "fixer_composio",
    "ComposioFlexisignAPIComponent": "flexisign_composio",
    "ComposioFreshdeskAPIComponent": "freshdesk_composio",
    "ComposioGitHubAPIComponent": "github_composio",
    "ComposioGmailAPIComponent": "gmail_composio",
    "ComposioGoogleBigQueryAPIComponent": "googlebigquery_composio",
    "ComposioGoogleCalendarAPIComponent": "googlecalendar_composio",
    "ComposioGoogleclassroomAPIComponent": "googleclassroom_composio",
    "ComposioGoogleDocsAPIComponent": "googledocs_composio",
    "ComposioGooglemeetAPIComponent": "googlemeet_composio",
    "ComposioGoogleSheetsAPIComponent": "googlesheets_composio",
    "ComposioGoogleTasksAPIComponent": "googletasks_composio",
    "ComposioHeygenAPIComponent": "heygen_composio",
    "ComposioInstagramAPIComponent": "instagram_composio",
    "ComposioJiraAPIComponent": "jira_composio",
    "ComposioJotformAPIComponent": "jotform_composio",
    "ComposioKlaviyoAPIComponent": "klaviyo_composio",
    "ComposioLinearAPIComponent": "linear_composio",
    "ComposioListennotesAPIComponent": "listennotes_composio",
    "ComposioMem0APIComponent": "mem0_composio",
    "ComposioMiroAPIComponent": "miro_composio",
    "ComposioMissiveAPIComponent": "missive_composio",
    "ComposioNotionAPIComponent": "notion_composio",
    "ComposioOneDriveAPIComponent": "onedrive_composio",
    "ComposioOutlookAPIComponent": "outlook_composio",
    "ComposioPandadocAPIComponent": "pandadoc_composio",
    "ComposioTimelinesAIAPIComponent": "timelinesai_composio",
    "ComposioWrikeAPIComponent": "wrike_composio",
}

# 注意：统一暴露所有组件，单个导入失败由 `__getattr__` 处理
__all__ = [
    "ComposioAPIComponent",
    "ComposioAgentQLAPIComponent",
    "ComposioAgiledAPIComponent",
    "ComposioAirtableAPIComponent",
    "ComposioApolloAPIComponent",
    "ComposioAsanaAPIComponent",
    "ComposioAttioAPIComponent",
    "ComposioBitbucketAPIComponent",
    "ComposioBolnaAPIComponent",
    "ComposioBrightdataAPIComponent",
    "ComposioCalendlyAPIComponent",
    "ComposioCalendlyAPIComponent",
    "ComposioCanvaAPIComponent",
    "ComposioCanvasAPIComponent",
    "ComposioCodaAPIComponent",
    "ComposioContentfulAPIComponent",
    "ComposioDigicertAPIComponent",
    "ComposioDiscordAPIComponent",
    "ComposioElevenLabsAPIComponent",
    "ComposioExaAPIComponent",
    "ComposioFigmaAPIComponent",
    "ComposioFinageAPIComponent",
    "ComposioFirecrawlAPIComponent",
    "ComposioFirefliesAPIComponent",
    "ComposioFixerAPIComponent",
    "ComposioFlexisignAPIComponent",
    "ComposioFreshdeskAPIComponent",
    "ComposioGitHubAPIComponent",
    "ComposioGmailAPIComponent",
    "ComposioGoogleBigQueryAPIComponent",
    "ComposioGoogleCalendarAPIComponent",
    "ComposioGoogleDocsAPIComponent",
    "ComposioGoogleSheetsAPIComponent",
    "ComposioGoogleTasksAPIComponent",
    "ComposioGoogleclassroomAPIComponent",
    "ComposioGooglemeetAPIComponent",
    "ComposioHeygenAPIComponent",
    "ComposioInstagramAPIComponent",
    "ComposioJiraAPIComponent",
    "ComposioJotformAPIComponent",
    "ComposioKlaviyoAPIComponent",
    "ComposioKlaviyoAPIComponent",
    "ComposioLinearAPIComponent",
    "ComposioLinearAPIComponent",
    "ComposioListennotesAPIComponent",
    "ComposioMem0APIComponent",
    "ComposioMiroAPIComponent",
    "ComposioMissiveAPIComponent",
    "ComposioNotionAPIComponent",
    "ComposioOneDriveAPIComponent",
    "ComposioOutlookAPIComponent",
    "ComposioPandadocAPIComponent",
    "ComposioPeopleDataLabsAPIComponent",
    "ComposioPerplexityAIAPIComponent",
    "ComposioRedditAPIComponent",
    "ComposioSerpAPIComponent",
    "ComposioSlackAPIComponent",
    "ComposioSlackbotAPIComponent",
    "ComposioSnowflakeAPIComponent",
    "ComposioSupabaseAPIComponent",
    "ComposioTavilyAPIComponent",
    "ComposioTimelinesAIAPIComponent",
    "ComposioTodoistAPIComponent",
    "ComposioWrikeAPIComponent",
    "ComposioYoutubeAPIComponent",
]


def __getattr__(attr_name: str) -> Any:
    """按需导入 Composio 组件。

    契约：`attr_name` 必须在 `_dynamic_imports` 中注册。
    副作用：动态导入模块并写入 `globals()`。
    失败语义：缺少注册或导入失败时抛 `AttributeError`。
    """
    if attr_name not in _dynamic_imports:
        msg = f"module '{__name__}' has no attribute '{attr_name}'"
        raise AttributeError(msg)
    try:
        result = import_mod(attr_name, _dynamic_imports[attr_name], __spec__.parent)
    except (ModuleNotFoundError, ImportError, AttributeError) as e:
        msg = f"Could not import '{attr_name}' from '{__name__}': {e}"
        raise AttributeError(msg) from e
    globals()[attr_name] = result
    return result


def __dir__() -> list[str]:
    """提供可见导出列表，便于 `dir()` 与 IDE 补全。"""
    return list(__all__)
