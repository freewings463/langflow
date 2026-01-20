"""
模块名称：Loop 组件

本模块提供对 Data/Message 列表的循环处理能力，主要用于在流程中逐项处理并聚合结果。
主要功能包括：
- 输入 DataFrame/Data/Message 列表并逐项输出
- 维护上下文索引与聚合列表
- 在循环结束时输出聚合结果

关键组件：
- `LoopComponent`：循环组件

设计背景：在流程控制中提供可重复执行的迭代机制，并保持输出一致性。
注意事项：Message 会被自动转换为 Data 以保持类型一致。
"""

from lfx.components.processing.converter import convert_to_data
from lfx.custom.custom_component.component import Component
from lfx.inputs.inputs import HandleInput
from lfx.schema.data import Data
from lfx.schema.dataframe import DataFrame
from lfx.schema.message import Message
from lfx.template.field.base import Output


class LoopComponent(Component):
    """循环输出输入列表的单项并聚合结果。

    契约：`item_output` 输出单条 Data；`done_output` 输出聚合 DataFrame。
    副作用：在上下文中维护 `_data`/`_index`/`_aggregated` 状态。
    失败语义：输入类型不合法会抛 `TypeError`。
    """
    display_name = "Loop"
    description = (
        "Iterates over a list of Data or Message objects, outputting one item at a time and "
        "aggregating results from loop inputs. Message objects are automatically converted to "
        "Data objects for consistent processing."
    )
    documentation: str = "https://docs.langflow.org/loop"
    icon = "infinity"

    inputs = [
        HandleInput(
            name="data",
            display_name="Inputs",
            info="The initial DataFrame to iterate over.",
            input_types=["DataFrame"],
        ),
    ]

    outputs = [
        Output(
            display_name="Item",
            name="item",
            method="item_output",
            allows_loop=True,
            loop_types=["Message"],
            group_outputs=True,
        ),
        Output(display_name="Done", name="done", method="done_output", group_outputs=True),
    ]

    def initialize_data(self) -> None:
        """初始化数据列表、索引与聚合列表。"""
        if self.ctx.get(f"{self._id}_initialized", False):
            return

        # 实现：确保数据为 Data 列表
        data_list = self._validate_data(self.data)

        # 实现：写入初始上下文状态
        self.update_ctx(
            {
                f"{self._id}_data": data_list,
                f"{self._id}_index": 0,
                f"{self._id}_aggregated": [],
                f"{self._id}_initialized": True,
            }
        )

    def _convert_message_to_data(self, message: Message) -> Data:
        """将 Message 转为 Data（复用转换逻辑）。"""
        return convert_to_data(message, auto_parse=False)

    def _validate_data(self, data):
        """校验并返回 Data 列表，必要时将 Message 转为 Data。"""
        if isinstance(data, DataFrame):
            return data.to_data_list()
        if isinstance(data, Data):
            return [data]
        if isinstance(data, Message):
            # 实现：自动转换 Message 为 Data
            converted_data = self._convert_message_to_data(data)
            return [converted_data]
        if isinstance(data, list) and all(isinstance(item, (Data, Message)) for item in data):
            # 实现：列表内 Message 逐个转换为 Data
            converted_list = []
            for item in data:
                if isinstance(item, Message):
                    converted_list.append(self._convert_message_to_data(item))
                else:
                    converted_list.append(item)
            return converted_list
        msg = "The 'data' input must be a DataFrame, a list of Data/Message objects, or a single Data/Message object."
        raise TypeError(msg)

    def evaluate_stop_loop(self) -> bool:
        """判断是否应结束循环。"""
        current_index = self.ctx.get(f"{self._id}_index", 0)
        data_length = len(self.ctx.get(f"{self._id}_data", []))
        return current_index > data_length

    def item_output(self) -> Data:
        """输出当前项或在完成后停止循环。

        关键路径（三步）：
        1) 初始化上下文并读取当前索引
        2) 输出当前项并递增索引
        3) 更新依赖以触发下一轮
        异常流：索引越界时返回空 Data。
        性能瓶颈：与列表长度线性相关。
        """
        self.initialize_data()
        current_item = Data(text="")

        if self.evaluate_stop_loop():
            self.stop("item")
        else:
            # 实现：读取数据列表与当前索引
            data_list, current_index = self.loop_variables()
            if current_index < len(data_list):
                # 实现：输出当前项并递增索引
                try:
                    current_item = data_list[current_index]
                except IndexError:
                    current_item = Data(text="")
            self.aggregated_output()
            self.update_ctx({f"{self._id}_index": current_index + 1})

        # 实现：更新依赖以驱动下一轮执行
        self.update_dependency()
        return current_item

    def update_dependency(self):
        item_dependency_id = self.get_incoming_edge_by_target_param("item")
        if item_dependency_id not in self.graph.run_manager.run_predecessors[self._id]:
            self.graph.run_manager.run_predecessors[self._id].append(item_dependency_id)
            # 注意：同步更新 run_map 以确保 remove_from_predecessors() 正常工作
            if self._id not in self.graph.run_manager.run_map[item_dependency_id]:
                self.graph.run_manager.run_map[item_dependency_id].append(self._id)

    def done_output(self) -> DataFrame:
        """在迭代完成后输出聚合结果。

        关键路径：检查停止条件 → 输出聚合 DataFrame → 停止/启动分支。
        """
        self.initialize_data()

        if self.evaluate_stop_loop():
            self.stop("item")
            self.start("done")

            aggregated = self.ctx.get(f"{self._id}_aggregated", [])

            return DataFrame(aggregated)
        self.stop("done")
        return DataFrame([])

    def loop_variables(self):
        """从上下文读取循环变量。"""
        return (
            self.ctx.get(f"{self._id}_data", []),
            self.ctx.get(f"{self._id}_index", 0),
        )

    def aggregated_output(self) -> list[Data]:
        """聚合当前循环输入并返回聚合列表。

        关键路径（三步）：
        1) 读取上下文中的数据与聚合列表
        2) 规范化当前输入为 Data
        3) 追加并写回聚合列表
        异常流：无显式异常抛出。
        """
        self.initialize_data()

        # 实现：读取数据列表与聚合列表
        data_list = self.ctx.get(f"{self._id}_data", [])
        aggregated = self.ctx.get(f"{self._id}_aggregated", [])
        loop_input = self.item

        # 实现：追加当前输入到聚合列表
        if loop_input is not None and not isinstance(loop_input, str) and len(aggregated) <= len(data_list):
            # 注意：Message 转换为 Data 以保持一致性
            if isinstance(loop_input, Message):
                loop_input = self._convert_message_to_data(loop_input)
            aggregated.append(loop_input)
            self.update_ctx({f"{self._id}_aggregated": aggregated})
        return aggregated
