import json
from typing import Any

import requests
from langchain.tools import StructuredTool
from pydantic import BaseModel, Field

from lfx.base.langchain_utilities.model import LCToolComponent
from lfx.field_typing import Tool
from lfx.inputs.inputs import MultilineInput, SecretStrInput, StrInput
from lfx.schema.data import Data


class NotionPageCreator(LCToolComponent):
    # 创建 Notion 页面组件
    display_name: str = "Create Page "
    description: str = "A component for creating Notion pages."
    documentation: str = "https://docs.langflow.org/bundles-notion"
    icon = "NotionDirectoryLoader"

    inputs = [
        # 用户输入：数据库 ID、密钥、页面属性
        StrInput(
            name="database_id",
            display_name="Database ID",
            info="The ID of the Notion database.",
        ),
        SecretStrInput(
            name="notion_secret",
            display_name="Notion Secret",
            info="The Notion integration token.",
            required=True,
        ),
        MultilineInput(
            name="properties_json",
            display_name="Properties (JSON)",
            info="The properties of the new page as a JSON string.",
        ),
    ]

    class NotionPageCreatorSchema(BaseModel):
        # 工具入参 Schema
        database_id: str = Field(..., description="The ID of the Notion database.")
        properties_json: str = Field(..., description="The properties of the new page as a JSON string.")

    def run_model(self) -> Data:
        # 执行创建并转换为 Data 输出
        result = self._create_notion_page(self.database_id, self.properties_json)
        if isinstance(result, str):
            # An error occurred, return it as text
            return Data(text=result)
        # Success, return the created page data
        output = "Created page properties:\n"
        for prop_name, prop_value in result.get("properties", {}).items():
            output += f"{prop_name}: {prop_value}\n"
        return Data(text=output, data=result)

    def build_tool(self) -> Tool:
        # 以结构化工具形式暴露
        return StructuredTool.from_function(
            name="create_notion_page",
            description="Create a new page in a Notion database. "
            "IMPORTANT: Use the tool to check the Database properties for more details before using this tool.",
            func=self._create_notion_page,
            args_schema=self.NotionPageCreatorSchema,
        )

    def _create_notion_page(self, database_id: str, properties_json: str) -> dict[str, Any] | str:
        # 校验入参并解析属性 JSON
        if not database_id or not properties_json:
            return "Invalid input. Please provide 'database_id' and 'properties_json'."

        try:
            properties = json.loads(properties_json)
        except json.JSONDecodeError as e:
            return f"Invalid properties format. Please provide a valid JSON string. Error: {e}"

        # 构造 Notion API 请求
        headers = {
            "Authorization": f"Bearer {self.notion_secret}",
            "Content-Type": "application/json",
            "Notion-Version": "2022-06-28",
        }

        data = {
            "parent": {"database_id": database_id},
            "properties": properties,
        }

        try:
            # 发送创建页面请求
            response = requests.post("https://api.notion.com/v1/pages", headers=headers, json=data, timeout=10)
            response.raise_for_status()
            return response.json()
        except requests.exceptions.RequestException as e:
            error_message = f"Failed to create Notion page. Error: {e}"
            if hasattr(e, "response") and e.response is not None:
                error_message += f" Status code: {e.response.status_code}, Response: {e.response.text}"
            return error_message

    def __call__(self, *args, **kwargs):
        return self._create_notion_page(*args, **kwargs)
