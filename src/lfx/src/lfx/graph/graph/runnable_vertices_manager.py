"""模块名称：可运行顶点状态管理

本模块维护图运行过程中可执行顶点的状态与依赖关系。
使用场景：调度器在执行顶点时判断是否可运行与更新依赖。
主要功能包括：
- 维护前驱/后继关系
- 标记可运行、运行中与已运行的顶点
- 处理循环顶点的可运行判定
"""

from collections import defaultdict


class RunnableVerticesManager:
    """可运行顶点管理器。"""

    def __init__(self) -> None:
        # 注意：run_map 记录“前驱 -> 可解锁的后继”，用于快速移除依赖。
        self.run_map: dict[str, list[str]] = defaultdict(list)
        # 注意：run_predecessors 记录“顶点 -> 未完成的前驱”，用于判定可运行。
        self.run_predecessors: dict[str, list[str]] = defaultdict(list)
        # 注意：vertices_to_run 表示已满足前驱条件、待执行的顶点。
        self.vertices_to_run: set[str] = set()
        # 注意：vertices_being_run 表示当前执行中的顶点，避免重复调度。
        self.vertices_being_run: set[str] = set()
        # 注意：cycle_vertices 用于循环处理策略。
        self.cycle_vertices: set[str] = set()
        # 注意：ran_at_least_once 标记循环顶点是否已执行过一次。
        self.ran_at_least_once: set[str] = set()

    def to_dict(self) -> dict:
        """序列化运行状态为 dict。"""
        return {
            "run_map": self.run_map,
            "run_predecessors": self.run_predecessors,
            "vertices_to_run": self.vertices_to_run,
            "vertices_being_run": self.vertices_being_run,
            "ran_at_least_once": self.ran_at_least_once,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "RunnableVerticesManager":
        """从 dict 反序列化运行状态。"""
        instance = cls()
        instance.run_map = data["run_map"]
        instance.run_predecessors = data["run_predecessors"]
        instance.vertices_to_run = data["vertices_to_run"]
        instance.vertices_being_run = data["vertices_being_run"]
        instance.ran_at_least_once = data.get("ran_at_least_once", set())
        return instance

    def __getstate__(self) -> object:
        """pickle 序列化入口。"""
        return {
            "run_map": self.run_map,
            "run_predecessors": self.run_predecessors,
            "vertices_to_run": self.vertices_to_run,
            "vertices_being_run": self.vertices_being_run,
            "ran_at_least_once": self.ran_at_least_once,
        }

    def __setstate__(self, state: dict) -> None:
        """pickle 反序列化入口。"""
        self.run_map = state["run_map"]
        self.run_predecessors = state["run_predecessors"]
        self.vertices_to_run = state["vertices_to_run"]
        self.vertices_being_run = state["vertices_being_run"]
        self.ran_at_least_once = state["ran_at_least_once"]

    def all_predecessors_are_fulfilled(self) -> bool:
        """判断是否所有顶点都无未完成前驱。"""
        return all(not value for value in self.run_predecessors.values())

    def update_run_state(self, run_predecessors: dict, vertices_to_run: set) -> None:
        """更新前驱映射与可运行集合，并重建 run_map。"""
        self.run_predecessors.update(run_predecessors)
        self.vertices_to_run.update(vertices_to_run)
        self.build_run_map(self.run_predecessors, self.vertices_to_run)

    def is_vertex_runnable(self, vertex_id: str, *, is_active: bool, is_loop: bool = False) -> bool:
        """判断顶点是否可运行。"""
        if not is_active:
            return False
        if vertex_id in self.vertices_being_run:
            return False
        if vertex_id not in self.vertices_to_run:
            return False

        return self.are_all_predecessors_fulfilled(vertex_id, is_loop=is_loop)

    def are_all_predecessors_fulfilled(self, vertex_id: str, *, is_loop: bool) -> bool:
        """判断顶点前驱是否满足。

        契约：若无未完成前驱则可运行；循环顶点按循环策略放行
        失败语义：前驱未满足时返回 False
        """
        # 注意：无待处理前驱时直接可运行。
        pending = self.run_predecessors.get(vertex_id, [])
        if not pending:
            return True

        # 注意：循环顶点需避免互相等待造成死锁。
        if vertex_id in self.cycle_vertices:
            pending_set = set(pending)
            running_predecessors = pending_set & self.vertices_being_run

            # 注意：循环顶点已执行过一次时，需等待所有前驱清空。
            if vertex_id in self.ran_at_least_once:
                return not (pending_set or running_predecessors)

            # 注意：首次执行的循环顶点，仅在 loop 且前驱均为循环顶点时放行。
            return is_loop and pending_set <= self.cycle_vertices
        return False

    def remove_from_predecessors(self, vertex_id: str) -> None:
        """从所有后继的前驱列表中移除当前顶点。"""
        predecessors = self.run_map.get(vertex_id, [])
        for predecessor in predecessors:
            if vertex_id in self.run_predecessors[predecessor]:
                self.run_predecessors[predecessor].remove(vertex_id)

    def build_run_map(self, predecessor_map, vertices_to_run) -> None:
        """构建“前驱 -> 后继”的可运行映射。"""
        self.run_map = defaultdict(list)
        for vertex_id, predecessors in predecessor_map.items():
            for predecessor in predecessors:
                self.run_map[predecessor].append(vertex_id)
        self.run_predecessors = predecessor_map.copy()
        self.vertices_to_run = vertices_to_run

    def update_vertex_run_state(self, vertex_id: str, *, is_runnable: bool) -> None:
        """更新单个顶点的可运行状态。"""
        if is_runnable:
            self.vertices_to_run.add(vertex_id)
        else:
            self.vertices_being_run.discard(vertex_id)

    def remove_vertex_from_runnables(self, v_id) -> None:
        """移除顶点的可运行状态并清理其前驱影响。"""
        self.update_vertex_run_state(v_id, is_runnable=False)
        self.remove_from_predecessors(v_id)

    def add_to_vertices_being_run(self, v_id) -> None:
        """标记顶点为运行中。"""
        self.vertices_being_run.add(v_id)

    def add_to_cycle_vertices(self, v_id):
        """将顶点加入循环集合。"""
        self.cycle_vertices.add(v_id)
