"""
模块名称：Data 键提取组件（已停用）

本模块提供从 `Data` 对象中提取指定键的能力，主要用于旧流程的字段裁剪。主要功能包括：
- 按键名提取属性并构造新的 `Data`

关键组件：
- `ExtractKeyFromDataComponent`：键提取组件

设计背景：在旧版流程中用于简化 `Data` 输出。
注意事项：可通过 `silent_error` 控制是否忽略缺失键。
"""

from lfx.custom.custom_component.custom_component import CustomComponent
from lfx.schema.data import Data


class ExtractKeyFromDataComponent(CustomComponent):
    """Data 键提取组件。

    契约：输入 `Data` 与键列表，输出仅包含指定键的新 `Data`。
    失败语义：`silent_error=False` 时缺失键抛 `KeyError`。
    副作用：更新组件 `status`。
    """
    display_name = "Extract Key From Data"
    description = "Extracts a key from a data."
    beta: bool = True
    name = "ExtractKeyFromData"

    field_config = {
        "data": {"display_name": "Data"},
        "keys": {
            "display_name": "Keys",
            "info": "The keys to extract from the data.",
            "input_types": [],
        },
        "silent_error": {
            "display_name": "Silent Errors",
            "info": "If True, errors will not be raised.",
            "advanced": True,
        },
    }

    def build(self, data: Data, keys: list[str], *, silent_error: bool = True) -> Data:
        """从 `Data` 中提取指定键并返回新 `Data`。

        契约：仅复制指定键，不存在的键在静默模式下被忽略。
        失败语义：`silent_error=False` 且键不存在时抛 `KeyError`。
        副作用：更新组件 `status`。
        """
        extracted_keys = {}
        for key in keys:
            try:
                extracted_keys[key] = getattr(data, key)
            except AttributeError as e:
                if not silent_error:
                    msg = f"The key '{key}' does not exist in the data."
                    raise KeyError(msg) from e
        return_data = Data(data=extracted_keys)
        self.status = return_data
        return return_data
