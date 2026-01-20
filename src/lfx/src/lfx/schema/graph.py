"""图输入/输出相关 schema。"""

from typing import Any

from pydantic import BaseModel, Field, RootModel

from lfx.schema.schema import InputType


class InputValue(BaseModel):
    """定义图的输入值。

    关键路径（三步）：
    1) 定义组件列表
    2) 定义输入值
    3) 定义应用类型
    
    异常流：无显式异常处理。
    性能瓶颈：无。
    排障入口：无特定日志输出。
    """
    components: list[str] | None = []
    input_value: str | None = None
    type: InputType | None = Field(
        "any",
        description="Defines on which components the input value should be applied. "
        "'any' applies to all input components.",
    )


class Tweaks(RootModel):
    """定义流程调整参数。

    关键路径（三步）：
    1) 定义调整参数的根字典
    2) 提供字典访问方法（__getitem__, __setitem__, __delitem__）
    3) 提供字典遍历方法（items）
    
    异常流：无显式异常处理。
    性能瓶颈：无。
    排障入口：无特定日志输出。
    """
    root: dict[str, str | dict[str, Any]] = Field(
        description="A dictionary of tweaks to adjust the flow's execution. "
        "Allows customizing flow behavior dynamically. "
        "All tweaks are overridden by the input values.",
    )
    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "parameter_name": "value",
                    "Component Name": {"parameter_name": "value"},
                    "component_id": {"parameter_name": "value"},
                }
            ]
        }
    }

    # 使 Tweaks 表现为字典
    def __getitem__(self, key):
        """获取调整参数值。

        契约：
        - 输入：键
        - 输出：对应的值
        - 副作用：无
        - 失败语义：键不存在时抛出 KeyError
        """
        return self.root[key]

    def __setitem__(self, key, value) -> None:
        """设置调整参数值。

        契约：
        - 输入：键和值
        - 输出：无
        - 副作用：修改内部字典
        - 失败语义：无
        """
        self.root[key] = value

    def __delitem__(self, key) -> None:
        """删除调整参数。

        契约：
        - 输入：键
        - 输出：无
        - 副作用：从内部字典中删除键值对
        - 失败语义：键不存在时抛出 KeyError
        """
        del self.root[key]

    def items(self):
        """获取调整参数的键值对。

        契约：
        - 输入：无
        - 输出：键值对迭代器
        - 副作用：无
        - 失败语义：无
        """
        return self.root.items()
