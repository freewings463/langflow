"""
模块名称：状态服务实现

本模块定义状态服务抽象与内存实现，用于在运行期记录状态并通知订阅者。主要功能包括：
- 提供 `StateService` 抽象接口，约束状态读写与订阅契约
- 提供 `InMemoryStateService` 作为默认内存态实现

关键组件：
- `StateService`
- `InMemoryStateService`

使用场景：组件运行或流程执行中，需要共享状态或监听变化。
设计背景：以轻量内存态方案满足默认运行需求。
注意事项：内存实现不持久化，适用于单进程或短生命周期场景。
"""

from collections import defaultdict
from collections.abc import Callable
from threading import Lock

from lfx.log.logger import logger
from lfx.services.settings.service import SettingsService

from langflow.services.base import Service


class StateService(Service):
    """状态服务抽象接口，定义状态读写与订阅能力。

    契约：实现必须支持 `append_state` 与 `update_state` 两种写入语义，并提供订阅回调。
    关键职责：定义状态读写与订阅接口的最小语义。
    失败语义：由具体实现决定；抽象层不做错误处理。
    决策：抽象层仅定义语义不提供默认实现
    问题：不同存储后端的行为差异较大
    方案：接口层只约束输入/输出与回调语义
    代价：实现类需自行处理并发与持久化
    重评：当默认实现稳定后可下沉通用逻辑
    """

    name = "state_service"

    def append_state(self, key, new_state, run_id: str) -> None:
        """追加状态并触发追加通知。

        契约：按 `run_id` 分区追加；订阅回调需接收 `append=True`。
        关键路径：1) 追加状态 2) 通知订阅者。
        副作用：写入状态并触发订阅回调。
        失败语义：具体实现可选择抛错或降级为日志。
        决策：接口区分追加与覆盖两类写入
        问题：调用方需要显式表达累积或覆盖语义
        方案：提供 `append_state` 单独入口
        代价：接口面更大、实现成本更高
        重评：当仅需覆盖语义时考虑合并接口
        """
        raise NotImplementedError

    def update_state(self, key, new_state, run_id: str) -> None:
        """覆盖状态并触发更新通知。

        契约：覆盖指定 `key` 的当前值；订阅回调接收 `append=False`。
        关键路径：1) 写入新值 2) 通知订阅者。
        副作用：更新存储并触发订阅回调。
        失败语义：具体实现可抛出异常或静默处理。
        决策：覆盖写入不保留历史
        问题：默认状态应反映最新值而非历史序列
        方案：直接替换旧值并通知
        代价：历史值丢失
        重评：当需要审计历史时引入版本或日志
        """
        raise NotImplementedError

    def get_state(self, key, run_id: str):
        """读取指定 `run_id` 与 `key` 的状态。

        契约：返回当前状态值，缺省值由实现定义。
        关键路径：从存储中读取并返回对应值。
        副作用：无。
        失败语义：可返回缺省值或抛异常，取决于实现策略。
        决策：读取接口不强制类型或结构
        问题：不同调用场景的状态结构差异大
        方案：保持返回值为 `Any`
        代价：调用方需要自行校验类型
        重评：当状态模型稳定后增加类型约束
        """
        raise NotImplementedError

    def subscribe(self, key, observer: Callable) -> None:
        """订阅指定 `key` 的状态变更。

        契约：回调签名需支持 `(key, new_state, append)`。
        关键路径：注册观察者到订阅列表。
        副作用：注册观察者到订阅列表。
        失败语义：可拒绝无效回调或记录警告。
        决策：订阅以 `key` 为粒度
        问题：全局订阅会引入额外过滤成本
        方案：按 `key` 分组订阅
        代价：同一观察者需多次订阅多个 `key`
        重评：当订阅规模增长时引入通配订阅
        """
        raise NotImplementedError

    def unsubscribe(self, key, observer: Callable) -> None:
        """取消订阅指定 `key` 的状态变更。

        契约：未订阅的观察者应被安全忽略。
        关键路径：从订阅列表移除观察者。
        副作用：从订阅列表移除观察者。
        失败语义：实现可选择静默忽略或记录调试信息。
        决策：取消订阅不视为错误
        问题：并发场景下可能重复取消
        方案：安全忽略不存在的订阅
        代价：无法区分“未订阅”与“已移除”
        重评：当需要严格一致性时改为显式报错
        """
        raise NotImplementedError

    def notify_observers(self, key, new_state) -> None:
        """通知订阅者状态更新。

        契约：调用订阅回调并传递 `append=False`。
        关键路径：遍历订阅列表并执行回调。
        副作用：执行回调函数。
        失败语义：由具体实现决定。
        决策：由实现决定异常处理策略
        问题：回调异常可能影响主流程
        方案：交由实现选择捕获或抛出
        代价：接口层无法保证一致行为
        重评：当需要统一异常策略时收敛到基类
        """
        raise NotImplementedError


class InMemoryStateService(StateService):
    """内存态状态服务实现，线程安全且支持订阅回调。

    契约：状态存储按 `run_id` 分区；回调以 `key` 分组。
    关键职责：在内存中维护状态与观察者列表。
    失败语义：回调异常仅在追加通知中被捕获并记录。
    决策：默认使用内存字典作为存储介质
    问题：需要最低成本的状态服务实现
    方案：以进程内 `dict` + `Lock` 实现
    代价：无法跨进程共享或持久化
    重评：当需要分布式共享时替换为外部存储
    """

    def __init__(self, settings_service: SettingsService):
        """初始化内存状态与观察者列表。

        契约：`settings_service` 仅保存引用；状态存储按 `run_id` 分区。
        副作用：初始化内存字典与线程锁。
        失败语义：不抛异常。
        关键路径：1) 绑定设置服务 2) 初始化 `states` 与 `observers` 3) 建立互斥锁。
        决策：使用 `Lock` 保护状态与订阅列表
        问题：多线程写入可能造成竞态
        方案：对关键读写加锁
        代价：高并发下可能降低吞吐
        重评：当并发显著提升时评估读写锁或无锁结构
        """
        self.settings_service = settings_service
        self.states: dict[str, dict] = {}
        self.observers: dict[str, list[Callable]] = defaultdict(list)
        self.lock = Lock()

    def append_state(self, key, new_state, run_id: str) -> None:
        """追加状态到列表，并触发追加型通知。

        契约：相同 `key` 会被提升为列表并追加；无返回值。
        副作用：更新内存状态并触发回调。
        失败语义：回调异常被捕获并记录日志。
        关键路径（三步）：1) 确保 `run_id` 与 `key` 存在 2) 追加新状态 3) 通知订阅者。
        异常流：回调异常不会中断主流程。
        排障入口：日志关键字 `Error in observer`。
        决策：不同类型的同名 `key` 被提升为列表
        问题：同一 `key` 既可能是单值也可能是多值
        方案：检测非列表时转换为列表再追加
        代价：下游读取需处理列表与单值差异
        重评：当状态类型稳定后统一为列表或单值
        """
        with self.lock:
            if run_id not in self.states:
                self.states[run_id] = {}
            if key not in self.states[run_id]:
                self.states[run_id][key] = []
            elif not isinstance(self.states[run_id][key], list):
                self.states[run_id][key] = [self.states[run_id][key]]
            self.states[run_id][key].append(new_state)
            self.notify_append_observers(key, new_state)

    def update_state(self, key, new_state, run_id: str) -> None:
        """覆盖状态值并触发更新通知。

        契约：无论原值类型如何均被覆盖；无返回值。
        副作用：更新内存状态并触发回调。
        失败语义：回调异常向上抛出（未捕获）。
        关键路径：1) 初始化 `run_id` 2) 写入新状态 3) 通知订阅者。
        决策：更新通知与追加通知分离
        问题：追加与覆盖需要不同的订阅语义
        方案：用 `append` 标志区分
        代价：回调实现需要额外分支
        重评：当订阅模型统一后合并通知接口
        """
        with self.lock:
            if run_id not in self.states:
                self.states[run_id] = {}
            self.states[run_id][key] = new_state
            self.notify_observers(key, new_state)

    def get_state(self, key, run_id: str):
        """读取当前状态。

        契约：不存在返回空字符串 `""`；不抛异常。
        关键路径：读取 `states[run_id][key]` 并回退缺省值。
        副作用：无。
        失败语义：无状态时返回空字符串。
        决策：缺省值为空字符串
        问题：调用方需要一个可用的默认值
        方案：返回 `""` 以避免 `KeyError`
        代价：无法区分“空值”与“无值”
        重评：当需要区分缺省与空值时改为 `None`
        """
        with self.lock:
            return self.states.get(run_id, {}).get(key, "")

    def subscribe(self, key, observer: Callable) -> None:
        """订阅指定 `key` 的状态变更。

        契约：同一观察者不会重复添加；无返回值。
        关键路径：检查去重后追加到 `observers[key]`。
        副作用：更新观察者列表。
        失败语义：不抛异常。
        决策：订阅列表进行去重
        问题：重复回调会导致多次通知
        方案：在添加前检查是否已存在
        代价：每次订阅需线性扫描列表
        重评：当订阅量增加时改为集合结构
        """
        with self.lock:
            if observer not in self.observers[key]:
                self.observers[key].append(observer)

    def notify_observers(self, key, new_state) -> None:
        """通知订阅者发生覆盖更新。

        契约：回调签名为 `(key, new_state, append=False)`。
        关键路径：遍历 `observers[key]` 并执行回调。
        副作用：执行回调函数。
        失败语义：回调异常会向上抛出。
        决策：覆盖更新不捕获回调异常
        问题：调用方需要感知回调失败
        方案：让异常上抛给上层处理
        代价：单个回调异常可能中断通知链
        重评：当需要隔离回调失败时改为捕获并记录
        """
        for callback in self.observers[key]:
            callback(key, new_state, append=False)

    def notify_append_observers(self, key, new_state) -> None:
        """通知订阅者发生追加更新，异常会记录日志。

        契约：回调签名为 `(key, new_state, append=True)`。
        关键路径：遍历订阅列表并逐个回调。
        副作用：执行回调函数并记录异常日志。
        失败语义：回调异常被捕获，主流程继续。
        决策：追加更新捕获回调异常
        问题：追加场景更容易触发高频回调
        方案：捕获异常并记录，保证状态追加不被阻断
        代价：异常被吞掉，调用方可能不感知
        重评：当需要严格失败反馈时改为上抛
        """
        for callback in self.observers[key]:
            try:
                callback(key, new_state, append=True)
            except Exception:  # noqa: BLE001
                logger.exception(f"Error in observer {callback} for key {key}")
                logger.warning("Callbacks not implemented yet")

    def unsubscribe(self, key, observer: Callable) -> None:
        """取消订阅指定 `key` 的状态变更。

        契约：若观察者不存在则静默忽略。
        关键路径：在 `observers[key]` 中移除观察者。
        副作用：更新观察者列表。
        失败语义：不抛异常。
        决策：移除失败不视为错误
        问题：并发或重复取消会导致不存在
        方案：安全忽略并保持幂等
        代价：无法区分逻辑错误与正常忽略
        重评：当需要强一致性时记录警告
        """
        with self.lock:
            if observer in self.observers[key]:
                self.observers[key].remove(observer)
