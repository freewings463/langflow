"""
模块名称：工具常量

本模块集中定义工具相关的 `UI` 常量与字段约定，供工具配置面板与运行逻辑复用。
主要功能包括：
- 工具输出名称与展示名
- 工具元数据表结构
- 工具更新触发字段列表

关键组件：TOOL_TABLE_SCHEMA、TOOL_UPDATE_CONSTANTS
设计背景：统一工具配置与展示的字段规范
注意事项：编辑模式与隐藏字段与前端表格渲染强关联
"""

from lfx.schema.table import EditMode

TOOL_OUTPUT_NAME = "component_as_tool"
TOOL_OUTPUT_DISPLAY_NAME = "Toolset"
TOOLS_METADATA_INPUT_NAME = "tools_metadata"
# 注意：工具元数据表格结构，需与前端渲染约定一致。
TOOL_TABLE_SCHEMA = [
    {
        "name": "name",
        "display_name": "Tool Name",
        "type": "str",
        "description": "Specify the name of the tool.",
        "sortable": False,
        "filterable": False,
        "edit_mode": EditMode.INLINE,
        "hidden": False,
    },
    {
        "name": "description",
        "display_name": "Tool Description",
        "type": "str",
        "description": "Describe the purpose of the tool.",
        "sortable": False,
        "filterable": False,
        "edit_mode": EditMode.POPOVER,
        "hidden": False,
    },
    {
        "name": "tags",
        "display_name": "Tool Identifiers",
        "type": "str",
        "description": ("The default identifiers for the tools and cannot be changed."),
        "disable_edit": True,
        "sortable": False,
        "filterable": False,
        "edit_mode": EditMode.INLINE,
        "hidden": True,
    },
    {
        "name": "status",
        "display_name": "Enable",
        "type": "boolean",
        "description": "Indicates whether the tool is currently active. Set to True to activate this tool.",
        "default": True,
    },
]

TOOLS_METADATA_INFO = "Modify tool names and descriptions to help agents understand when to use each tool."

# 注意：触发工具元数据更新的字段白名单。
TOOL_UPDATE_CONSTANTS = ["tool_mode", "tool_actions", TOOLS_METADATA_INPUT_NAME, "flow_name_selected"]
