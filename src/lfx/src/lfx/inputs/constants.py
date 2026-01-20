"""
模块名称：输入组件常量

本模块集中定义输入组件的固定限制值。
主要功能包括：
- 约束 Tab 选项数量与单项长度

关键组件：
- `MAX_TAB_OPTIONS`
- `MAX_TAB_OPTION_LENGTH`

设计背景：将 UI 约束统一收敛到常量便于复用。
注意事项：调整后需同步更新前端校验逻辑。
"""

# TabInput 最大可选项数量
MAX_TAB_OPTIONS = 3
# 单个 Tab 选项的最大长度
MAX_TAB_OPTION_LENGTH = 20
