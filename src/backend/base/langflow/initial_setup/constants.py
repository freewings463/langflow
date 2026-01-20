"""模块名称：初始配置常量

模块目的：集中管理初始项目与助手文件夹的展示名称与描述。
主要功能：提供 `starter`/`assistant` 文件夹的名称与说明文案。
使用场景：初始化数据库与前端展示默认文件夹。
关键组件：`STARTER_FOLDER_NAME`、`ASSISTANT_FOLDER_NAME`
设计背景：避免多处硬编码导致文案不一致。
注意事项：修改文案需同步检查前端展示与迁移脚本兼容性。
"""

STARTER_FOLDER_NAME = "Starter Projects"
STARTER_FOLDER_DESCRIPTION = "Starter projects to help you get started in Langflow."

ASSISTANT_FOLDER_NAME = "Langflow Assistant"
ASSISTANT_FOLDER_DESCRIPTION = "Pre-built flows from Langflow Assistant to enhance your workflow."
