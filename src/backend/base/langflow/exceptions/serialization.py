"""
模块名称：序列化异常封装

本模块提供面向 API 的序列化错误包装，主要用于将组件输出的 `JSON` 序列化失败转为可操作的提示。
主要功能包括：
- `SerializationError`：携带用户可读的 `detail` 与原始异常。
- `from_exception`：识别常见失败模式并生成更具体的修复建议。

关键组件：`SerializationError`。
设计背景：组件输出不合法时需要给出可执行的修复路径。
使用场景：组件返回值无法序列化为 `JSON`。
注意事项：提示依赖错误文本关键字，运行时错误信息变更需同步更新。
"""

from typing import Any

from fastapi import HTTPException, status


class SerializationError(HTTPException):
    """序列化失败的 API 异常包装。

    契约：输入 `detail`/`status_code`，输出为 `HTTPException`；副作用：无。
    失败语义：异常本身即错误响应，`original_error` 保留根因便于排障。
    """

    def __init__(
        self,
        detail: str,
        original_error: Exception | None = None,
        data: Any = None,
        status_code: int = status.HTTP_500_INTERNAL_SERVER_ERROR,
    ) -> None:
        """初始化序列化异常。

        契约：`detail` 为用户可读提示，`original_error` 保留根因，`data` 为上下文快照。
        副作用：无；失败语义：不额外抛出新异常。
        """
        super().__init__(status_code=status_code, detail=detail)
        self.original_error = original_error
        self.data = data

    @classmethod
    def from_exception(cls, exc: Exception, data: Any = None) -> "SerializationError":
        """从已有异常生成可操作的序列化提示。

        契约：输入 `exc` 与可选 `data`，输出 `SerializationError`；副作用：无。
        关键路径（三步）：
        1) 解析 `exc.args`，提取结构化错误列表。
        2) 识别 `TypeError` 并匹配关键字（`'coroutine'`、`'vars()'`）。
        3) 生成针对性 `detail`，否则回退到通用提示。
        异常流：`exc.args` 为空时直接走通用提示。
        排障入口：提示文案引导检查 `await` 与 `JSON` 可序列化类型。
        """
        errors = exc.args[0] if exc.args else []

        if isinstance(errors, list):
            for error in errors:
                if isinstance(error, TypeError):
                    # 注意：基于错误文本关键字判定常见失败模式，需与运行时错误信息同步。
                    if "'coroutine'" in str(error):
                        return cls(
                            detail=(
                                "The component contains async functions that need to be awaited. Please add 'await' "
                                "before any async function calls in your component code."
                            ),
                            original_error=exc,
                            data=data,
                        )
                    if "vars()" in str(error):
                        return cls(
                            detail=(
                                "The component contains objects that cannot be converted to JSON. Please ensure all "
                                "properties and return values in your component are basic Python types like strings, "
                                "numbers, lists, or dictionaries."
                            ),
                            original_error=exc,
                            data=data,
                        )

        # 注意：未命中已知模式时返回通用提示，避免暴露过多内部细节。
        return cls(
            detail=(
                "The component returned invalid data. Please check that all values in your component (properties, "
                "return values, etc.) are basic Python types that can be converted to JSON."
            ),
            original_error=exc,
            data=data,
        )
