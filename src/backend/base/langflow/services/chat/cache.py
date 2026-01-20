"""
模块名称：聊天缓存与观察者

本模块提供同步/异步观察者基类以及按 `client_id` 分片的缓存服务，用于聊天会话内的临时数据存取。主要功能包括：
- 观察者模式：`Subject`/`AsyncSubject` 提供订阅与通知
- 客户端缓存：`CacheService` 按 `client_id` 管理缓存并在变更时通知

关键组件：
- `Subject` / `AsyncSubject`
- `CacheService`

设计背景：聊天输出需要按会话隔离缓存并触发界面刷新或自动化链路。
注意事项：缓存不做持久化；`get`/`get_last` 在缺失时会抛 `KeyError`/`IndexError`。
"""

from collections.abc import Awaitable, Callable
from contextlib import contextmanager
from typing import Any

import pandas as pd
from PIL import Image

from langflow.services.base import Service


class Subject:
    """同步观察者基类，用于注册与通知回调。

    契约：维护 `observers` 列表，回调签名为 `Callable[[], None]`。
    副作用：`notify` 会执行回调；失败语义：回调异常向上传播。
    关键路径（三步）：1) 注册/移除回调 2) 遍历列表 3) 顺序执行回调
    决策：使用顺序通知而非并发触发
    问题：并发触发需要额外调度与异常聚合
    方案：按注册顺序逐个调用
    代价：慢回调会阻塞后续通知
    重评：当通知耗时显著或需并行时
    """

    def __init__(self) -> None:
        """初始化观察者列表。

        契约：创建空的 `observers` 列表。
        副作用：分配新列表；失败语义：无显式失败。
        关键路径（三步）：1) 创建列表 2) 赋值属性 3) 返回
        决策：使用 `list` 存储观察者
        问题：需要保持注册顺序
        方案：采用 `list` 作为顺序容器
        代价：删除/查找为 `O(n)`
        重评：当观察者数量显著增长时
        """
        self.observers: list[Callable[[], None]] = []

    def attach(self, observer: Callable[[], None]) -> None:
        """注册观察者回调。

        契约：`observer` 为无参可调用对象；允许重复注册。
        副作用：追加 `observers`；失败语义：不校验可调用性，错误延迟到 `notify`。
        关键路径（三步）：1) 接收回调 2) 追加列表 3) 返回
        决策：不做运行时类型校验
        问题：每次校验会增加开销
        方案：信任调用方并直接存储
        代价：误用在通知阶段暴露
        重评：当误用频繁或需强校验时
        """
        self.observers.append(observer)

    def detach(self, observer: Callable[[], None]) -> None:
        """移除观察者回调。

        契约：`observer` 必须已注册。
        副作用：从 `observers` 删除；失败语义：缺失时抛 `ValueError`。
        关键路径（三步）：1) 接收回调 2) 列表移除 3) 返回
        决策：依赖 `list.remove` 维持顺序
        问题：需要保持注册顺序一致
        方案：使用列表线性删除
        代价：删除为 `O(n)` 成本
        重评：当观察者数量大且移除频繁时
        """
        self.observers.remove(observer)

    def notify(self) -> None:
        """通知全部观察者。

        契约：按注册顺序同步调用，`None` 回调会被跳过。
        副作用：执行回调；失败语义：任一回调异常中断通知。
        关键路径（三步）：1) 遍历列表 2) 跳过 `None` 3) 调用回调
        决策：跳过 `None` 而非抛错
        问题：回调可能被外部置空
        方案：空值直接忽略
        代价：潜在隐藏注册管理问题
        重评：当需要严格检测回调一致性时
        """
        for observer in self.observers:
            if observer is None:
                continue
            observer()


class AsyncSubject:
    """异步观察者基类。

    契约：维护 `observers` 列表，回调签名为 `Callable[[], Awaitable]`。
    副作用：`notify` 会 `await` 回调；失败语义：回调异常向上传播。
    关键路径（三步）：1) 注册/移除回调 2) 遍历列表 3) 顺序 `await`
    决策：顺序 `await` 而非并发 `gather`
    问题：并发需要异常聚合与取消策略
    方案：按顺序逐个等待
    代价：慢回调会阻塞后续通知
    重评：当观察者数量大且耗时显著时
    """

    def __init__(self) -> None:
        """初始化异步观察者列表。

        契约：创建空的 `observers` 列表。
        副作用：分配新列表；失败语义：无显式失败。
        关键路径（三步）：1) 创建列表 2) 赋值属性 3) 返回
        决策：使用 `list` 存储异步观察者
        问题：需要保持注册顺序以保证通知顺序
        方案：采用 `list` 作为顺序容器
        代价：删除/查找为 `O(n)`
        重评：当观察者数量显著增长时
        """
        self.observers: list[Callable[[], Awaitable]] = []

    def attach(self, observer: Callable[[], Awaitable]) -> None:
        """注册异步观察者回调。

        契约：`observer` 为无参可等待对象；允许重复注册。
        副作用：追加 `observers`；失败语义：不校验可等待性，错误延迟到 `notify`。
        关键路径（三步）：1) 接收回调 2) 写入列表 3) 返回
        决策：注册阶段不触发事件循环校验
        问题：注册时触发 `await` 会增加协程调度成本
        方案：仅存储回调引用并在通知时处理
        代价：错误延迟到通知阶段暴露
        重评：当需要早失败或提高可观测性时
        """
        self.observers.append(observer)

    def detach(self, observer: Callable[[], Awaitable]) -> None:
        """移除异步观察者回调。

        契约：`observer` 必须已注册。
        副作用：从 `observers` 删除；失败语义：缺失时抛 `ValueError`。
        关键路径（三步）：1) 接收回调 2) 线性移除 3) 返回
        决策：保留注册顺序以保证通知顺序
        问题：异步回调的副作用可能依赖顺序
        方案：使用列表并按注册顺序管理
        代价：移除为 `O(n)` 成本
        重评：当需要高频移除或大量回调时
        """
        self.observers.remove(observer)

    async def notify(self) -> None:
        """通知全部异步观察者。

        契约：按注册顺序 `await` 调用，`None` 回调会被跳过。
        副作用：执行回调；失败语义：任一回调异常中断通知。
        关键路径（三步）：1) 遍历列表 2) 跳过 `None` 3) 逐个 `await`
        决策：顺序 `await` 以保持副作用顺序
        问题：并发通知会打乱回调副作用顺序
        方案：在同一协程中逐个等待
        代价：整体通知耗时受最慢回调影响
        重评：当需要并发通知且无顺序依赖时
        """
        for observer in self.observers:
            if observer is None:
                continue
            await observer()


class CacheService(Subject, Service):
    """按 `client_id` 管理缓存并在变更时通知观察者。

    契约：`set_client_id` 作用域内的读写仅作用于当前 `client_id`。
    副作用：写入缓存会触发 `notify`；失败语义：类型不符抛 `TypeError`。
    关键路径（三步）：1) 选择客户端缓存 2) 读写条目 3) 通知观察者
    决策：缓存按 `client_id` 分片存储
    问题：不同会话需要隔离临时数据
    方案：以 `client_id` 为键维护独立字典
    代价：内存占用随客户端数量增长
    重评：当需要跨进程共享或持久化时
    """

    name = "cache_service"

    def __init__(self) -> None:
        """初始化缓存状态与当前客户端指针。

        契约：创建空 `_cache` 并将 `current_client_id` 置为 `None`。
        副作用：分配缓存字典；失败语义：无显式失败。
        关键路径（三步）：1) 初始化 `_cache` 2) 初始化当前指针 3) 返回
        决策：以字典承载每个 `client_id` 的子缓存
        问题：需要在同一进程内隔离不同客户端数据
        方案：使用 `dict[client_id] -> dict` 结构
        代价：客户端增多时内存占用上升
        重评：当需要外部缓存或持久化时
        """
        super().__init__()
        self._cache: dict[str, Any] = {}
        self.current_client_id: str | None = None
        self.current_cache: dict[str, Any] = {}

    @contextmanager
    def set_client_id(self, client_id: str):
        """在上下文内切换当前客户端缓存。

        契约：进入上下文后 `current_cache` 指向该 `client_id` 的字典。
        副作用：修改 `current_client_id`/`current_cache`；失败语义：异常不会阻止恢复。
        关键路径（三步）：1) 保存旧 `client_id` 2) 切换缓存 3) 退出时恢复
        决策：使用 `contextmanager` 自动恢复状态
        问题：手动恢复易遗漏导致串数据
        方案：在 `finally` 中恢复旧上下文
        代价：嵌套上下文需严格遵循作用域
        重评：当需要并发多客户端上下文时
        """
        previous_client_id = self.current_client_id
        self.current_client_id = client_id
        self.current_cache = self._cache.setdefault(client_id, {})
        try:
            yield
        finally:
            self.current_client_id = previous_client_id
            self.current_cache = self._cache.setdefault(previous_client_id, {}) if previous_client_id else {}

    def add(self, name: str, obj: Any, obj_type: str, extension: str | None = None) -> None:
        """写入当前客户端缓存并通知观察者。

        契约：`name` 为键，`obj_type` 用于扩展名推断。
        副作用：更新缓存并触发 `notify`；失败语义：未做类型校验。
        关键路径（三步）：1) 计算扩展名 2) 写入缓存 3) 通知观察者
        决策：内置 `image`/`pandas` 扩展名映射
        问题：部分类型需要稳定扩展名用于下载/展示
        方案：映射命中则使用固定扩展名
        代价：其他类型扩展名依赖 `type(obj).__name__`
        重评：当需要更丰富的类型映射时
        """
        object_extensions = {
            "image": "png",
            "pandas": "csv",
        }
        extension_ = object_extensions[obj_type] if obj_type in object_extensions else type(obj).__name__.lower()
        self.current_cache[name] = {
            "obj": obj,
            "type": obj_type,
            "extension": extension or extension_,
        }
        self.notify()

    def add_pandas(self, name: str, obj: Any) -> None:
        """缓存 `pandas` 对象并序列化为 `CSV` 字符串。

        契约：仅接受 `pd.DataFrame`/`pd.Series`。
        副作用：调用 `to_csv` 并写入缓存；失败语义：类型不符抛 `TypeError`。
        关键路径（三步）：1) 类型检查 2) 序列化为 `CSV` 3) 写入缓存
        决策：缓存为文本 `CSV` 而非二进制
        问题：缓存需可序列化且便于前端展示
        方案：使用 `to_csv` 生成字符串
        代价：读取端需自行反序列化
        重评：当需要保留类型信息或大数据压缩时
        """
        if isinstance(obj, pd.DataFrame | pd.Series):
            self.add(name, obj.to_csv(), "pandas", extension="csv")
        else:
            msg = "Object is not a pandas DataFrame or Series"
            raise TypeError(msg)

    def add_image(self, name: str, obj: Any, extension: str = "png") -> None:
        """缓存 `PIL.Image` 对象并标记扩展名。

        契约：`obj` 必须为 `Image.Image`；`extension` 默认为 `png`。
        副作用：写入缓存并触发 `notify`；失败语义：类型不符抛 `TypeError`。
        关键路径（三步）：1) 类型检查 2) 写入缓存 3) 通知观察者
        决策：默认扩展名为 `png`
        问题：多数图像导出需要稳定扩展名
        方案：提供默认值并允许覆盖
        代价：扩展名可能与实际编码不一致
        重评：当需要按实际编码自动识别时
        """
        if isinstance(obj, Image.Image):
            self.add(name, obj, "image", extension=extension)
        else:
            msg = "Object is not a PIL Image"
            raise TypeError(msg)

    def get(self, name: str):
        """读取当前客户端缓存条目。

        契约：`name` 必须存在于 `current_cache`。
        副作用：无；失败语义：缺失时抛 `KeyError`。
        关键路径（三步）：1) 查找键 2) 读取值 3) 返回
        决策：直接抛 `KeyError` 不做兜底
        问题：调用方需要区分未命中与业务空值
        方案：沿用 `dict` 的异常语义
        代价：调用方需显式捕获异常
        重评：当需要返回默认值或软失败时
        """
        return self.current_cache[name]

    def get_last(self):
        """读取当前客户端最后写入的缓存条目。

        契约：缓存至少包含一条记录。
        副作用：无；失败语义：为空时抛 `IndexError`。
        关键路径（三步）：1) 获取 values 列表 2) 取最后一项 3) 返回
        决策：依赖 `dict` 插入顺序
        问题：需要快速获得最近写入结果
        方案：使用 `list(values())[-1]`
        代价：创建列表有 `O(n)` 复制开销
        重评：当缓存规模增大或性能敏感时
        """
        return list(self.current_cache.values())[-1]


cache_service = CacheService()
