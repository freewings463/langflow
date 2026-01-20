"""
模块名称：Vertex 类型实现

模块目的：提供不同节点类型的具体行为（组件节点、接口节点、状态节点）。
使用场景：根据节点类型定制构建结果、消息提取与流式输出。
主要功能包括：
- 自定义组件节点与普通组件节点
- 接口节点的消息/流式处理
- 状态节点的简化构建流程

关键组件：
- `ComponentVertex` / `CustomComponentVertex`
- `InterfaceVertex`
- `StateVertex`

设计背景：不同节点类型需要差异化的结果包装与工件处理逻辑。
注意：接口节点包含流式处理与持久化副作用，修改需关注兼容性。
"""

from __future__ import annotations

import contextlib
import json
from collections.abc import AsyncIterator, Generator, Iterator
from typing import TYPE_CHECKING, Any, cast

import yaml
from langchain_core.messages import AIMessage, AIMessageChunk

from lfx.graph.schema import CHAT_COMPONENTS, RECORDS_COMPONENTS, InterfaceComponentTypes, ResultData
from lfx.graph.utils import UnbuiltObject, log_vertex_build, rewrite_file_path
from lfx.graph.vertex.base import Vertex
from lfx.graph.vertex.exceptions import NoComponentInstanceError
from lfx.log.logger import logger
from lfx.schema.artifact import ArtifactType
from lfx.schema.data import Data
from lfx.schema.message import Message
from lfx.schema.schema import INPUT_FIELD_NAME
from lfx.serialization.serialization import serialize
from lfx.template.field.base import UNDEFINED, Output
from lfx.utils.schemas import ChatOutputResponse, DataOutputResponse
from lfx.utils.util import unescape_string

if TYPE_CHECKING:
    from lfx.graph.edge.base import CycleEdge
    from lfx.graph.vertex.schema import NodeData
    from lfx.inputs.inputs import InputTypes


class CustomComponentVertex(Vertex):
    """自定义组件节点。"""

    def __init__(self, data: NodeData, graph):
        """初始化自定义组件节点。"""
        super().__init__(data, graph=graph, base_type="custom_components")

    def built_object_repr(self):
        """优先返回工件中的自定义展示文本。"""
        if self.artifacts and "repr" in self.artifacts:
            return self.artifacts["repr"] or super().built_object_repr()
        return None


class ComponentVertex(Vertex):
    """普通组件节点。"""

    def __init__(self, data: NodeData, graph):
        """初始化普通组件节点。"""
        super().__init__(data, graph=graph, base_type="component")

    def get_input(self, name: str) -> InputTypes:
        """获取组件输入定义。"""
        if self.custom_component is None:
            msg = f"Vertex {self.id} does not have a component instance."
            raise ValueError(msg)
        return self.custom_component.get_input(name)

    def get_output(self, name: str) -> Output:
        """获取组件输出定义。"""
        if self.custom_component is None:
            raise NoComponentInstanceError(self.id)
        return self.custom_component.get_output(name)

    def built_object_repr(self):
        """优先返回工件中的自定义展示文本。"""
        if self.artifacts and "repr" in self.artifacts:
            return self.artifacts["repr"] or super().built_object_repr()
        return None

    def _update_built_object_and_artifacts(self, result) -> None:
        """更新构建对象与工件，并写入节点结果。"""
        if isinstance(result, tuple):
            if len(result) == 2:  # noqa: PLR2004
                self.built_object, self.artifacts = result
            elif len(result) == 3:  # noqa: PLR2004
                self.custom_component, self.built_object, self.artifacts = result
                self.logs = self.custom_component.get_output_logs()
                for key in self.artifacts:
                    if self.artifacts_raw is None:
                        self.artifacts_raw = {}
                    self.artifacts_raw[key] = self.artifacts[key].get("raw", None)
                    self.artifacts_type[key] = self.artifacts[key].get("type", None) or ArtifactType.UNKNOWN.value
        else:
            self.built_object = result

        for key, value in self.built_object.items():
            self.add_result(key, value)

    def get_edge_with_target(self, target_id: str) -> Generator[CycleEdge]:
        """返回目标节点的边生成器。"""
        for edge in self.edges:
            if edge.target_id == target_id:
                yield edge

    async def _get_result(self, requester: Vertex, target_handle_name: str | None = None) -> Any:
        """获取构建结果（含未构建默认值逻辑）。"""
        if not self.built:
            default_value: Any = UNDEFINED
            for edge in self.get_edge_with_target(requester.id):
                if edge.is_cycle and edge.target_param:
                    if edge.target_param in requester.output_names:
                        default_value = None
                    else:
                        default_value = requester.get_value_from_template_dict(edge.target_param)

            if default_value is not UNDEFINED:
                return default_value
            msg = f"Component {self.display_name} has not been built yet"
            raise ValueError(msg)

        if requester is None:
            msg = "Requester Vertex is None"
            raise ValueError(msg)

        edges = self.get_edge_with_target(requester.id)
        result = UNDEFINED
        for edge in edges:
            if (
                edge is not None
                and edge.source_handle.name in self.results
                and edge.target_handle.field_name == target_handle_name
            ):
                try:
                    output = self.get_output(edge.source_handle.name)

                    if output.value is UNDEFINED:
                        result = self.results[edge.source_handle.name]
                    else:
                        result = cast("Any", output.value)
                except NoComponentInstanceError:
                    result = self.results[edge.source_handle.name]
                break
        if result is UNDEFINED:
            if edge is None:
                msg = f"Edge not found between {self.display_name} and {requester.display_name}"
                raise ValueError(msg)
            if edge.source_handle.name not in self.results:
                msg = f"Result not found for {edge.source_handle.name}. Results: {self.results}"
                raise ValueError(msg)
            msg = f"Result not found for {edge.source_handle.name} in {edge}"
            raise ValueError(msg)
        return result

    def extract_messages_from_artifacts(self, artifacts: dict[str, Any]) -> list[dict]:
        """从工件字典中抽取消息列表。"""
        messages = []
        for key, artifact in artifacts.items():
            if any(
                k not in artifact for k in ["text", "sender", "sender_name", "session_id", "stream_url"]
            ) and not isinstance(artifact, Message):
                continue
            message_dict = artifact if isinstance(artifact, dict) else artifact.model_dump()
            if not message_dict.get("text"):
                continue
            with contextlib.suppress(KeyError):
                messages.append(
                    ChatOutputResponse(
                        message=message_dict["text"],
                        sender=message_dict.get("sender"),
                        sender_name=message_dict.get("sender_name"),
                        session_id=message_dict.get("session_id"),
                        stream_url=message_dict.get("stream_url"),
                        files=[
                            {"path": file} if isinstance(file, str) else file for file in message_dict.get("files", [])
                        ],
                        component_id=self.id,
                        type=self.artifacts_type[key],
                    ).model_dump(exclude_none=True)
                )
        return messages

    def finalize_build(self) -> None:
        """封装 ResultData 并写入节点结果。"""
        result_dict = self.get_built_result()
        messages = self.extract_messages_from_artifacts(result_dict)
        result_dict = ResultData(
            results=result_dict,
            artifacts=self.artifacts,
            outputs=self.outputs_logs,
            logs=self.logs,
            messages=messages,
            component_display_name=self.display_name,
            component_id=self.id,
        )
        self.set_result(result_dict)


class InterfaceVertex(ComponentVertex):
    """接口类节点，负责消息与流式输出处理。"""

    def __init__(self, data: NodeData, graph):
        """初始化接口节点并调整构建步骤。"""
        super().__init__(data, graph=graph)
        self.added_message = None
        self.steps = [self._build, self._run]
        self.is_interface_component = True

    def build_stream_url(self) -> str:
        """生成流式输出 URL。"""
        return f"/api/v1/build/{self.graph.flow_id}/{self.id}/stream"

    def built_object_repr(self):
        """返回接口节点的可读展示文本。"""
        if self.task_id and self.is_task:
            if task := self.get_task():
                return str(task.info)
            return f"Task {self.task_id} is not running"
        if self.artifacts:
            if isinstance(self.artifacts, dict):
                artifacts_ = [self.artifacts]
            elif hasattr(self.artifacts, "data"):
                artifacts_ = self.artifacts.data
            else:
                artifacts_ = self.artifacts
            artifacts = []
            for artifact in artifacts_:
                artifact_ = {k.title().replace("_", " "): v for k, v in artifact.items() if v is not None}
                artifacts.append(artifact_)
            return yaml.dump(artifacts, default_flow_style=False, allow_unicode=True)
        return super().built_object_repr()

    def _process_chat_component(self):
        """处理聊天组件输出并生成消息工件。

        关键路径（三步）：
        1) 从参数中提取 sender/sender_name/消息与文件
        2) 根据输出类型构建 message 与 artifacts
        3) 回填 `self.artifacts` 并返回消息文本

        注意：当输出为迭代器时启用流式 URL。
        """
        artifacts = None
        sender = self.params.get("sender", None)
        sender_name = self.params.get("sender_name", None)
        message = self.params.get(INPUT_FIELD_NAME, None)
        files = self.params.get("files", [])
        treat_file_path = files is not None and not isinstance(files, list) and isinstance(files, str)
        if treat_file_path:
            self.params["files"] = rewrite_file_path(files)
        files = [{"path": file} if isinstance(file, str) else file for file in self.params.get("files", [])]
        if isinstance(message, str):
            message = unescape_string(message)
        stream_url = None
        if "text" in self.results:
            text_output = self.results["text"]
        elif "message" in self.results:
            text_output = self.results["message"].text
        else:
            text_output = message
        if isinstance(text_output, AIMessage | AIMessageChunk):
            artifacts = ChatOutputResponse.from_message(
                text_output,
                sender=sender,
                sender_name=sender_name,
            )
        elif not isinstance(text_output, UnbuiltObject):
            if isinstance(text_output, dict):
                message = dict_to_codeblock(text_output)
            elif isinstance(text_output, Data):
                message = text_output.text
            elif isinstance(message, AsyncIterator | Iterator):
                stream_url = self.build_stream_url()
                message = ""
                self.results["text"] = message
                self.results["message"].text = message
                self.built_object = self.results
            elif not isinstance(text_output, str):
                message = str(text_output)
            else:
                message = text_output

            if hasattr(sender_name, "get_text"):
                sender_name = sender_name.get_text()

            artifact_type = ArtifactType.STREAM if stream_url is not None else ArtifactType.OBJECT
            artifacts = ChatOutputResponse(
                message=message,
                sender=sender,
                sender_name=sender_name,
                stream_url=stream_url,
                files=files,
                type=artifact_type,
            )

            self.will_stream = stream_url is not None
        if artifacts:
            self.artifacts = artifacts.model_dump(exclude_none=True)

        return message

    def _process_data_component(self):
        """处理记录类组件输出并生成数据工件。

        契约：输出 `Data` 或 `list[Data]`，否则按 `ignore_errors` 处理。
        异常流：非 Data 且未忽略错误时抛 `ValueError`。
        """
        if isinstance(self.built_object, Data):
            artifacts = [self.built_object.data]
        elif isinstance(self.built_object, list):
            artifacts = []
            ignore_errors = self.params.get("ignore_errors", False)
            for value in self.built_object:
                if isinstance(value, Data):
                    artifacts.append(value.data)
                elif ignore_errors:
                    logger.error(f"Data expected, but got {value} of type {type(value)}")
                else:
                    msg = f"Data expected, but got {value} of type {type(value)}"
                    raise ValueError(msg)
        self.artifacts = DataOutputResponse(data=artifacts)
        return self.built_object

    async def _run(self, *args, **kwargs) -> None:  # noqa: ARG002
        """接口节点的后处理阶段。"""
        if self.vertex_type in CHAT_COMPONENTS:
            message = self._process_chat_component()
        elif self.vertex_type in RECORDS_COMPONENTS:
            message = self._process_data_component()
        if isinstance(self.built_object, AsyncIterator | Iterator):
            if self.params.get("return_data", False):
                self.built_object = Data(text=message, data=self.artifacts)
            else:
                self.built_object = message
        self.built_result = self.built_object

    async def stream(self):
        """消费消息流并生成最终 Message 与工件。"""
        iterator = self.params.get(INPUT_FIELD_NAME, None)
        if not isinstance(iterator, AsyncIterator | Iterator):
            msg = "The message must be an iterator or an async iterator."
            raise TypeError(msg)
        is_async = isinstance(iterator, AsyncIterator)
        complete_message = ""
        if is_async:
            async for message in iterator:
                message_ = message.content if hasattr(message, "content") else message
                message_ = message_.text if hasattr(message_, "text") else message_
                yield message_
                complete_message += message_
        else:
            for message in iterator:
                message_ = message.content if hasattr(message, "content") else message
                message_ = message_.text if hasattr(message_, "text") else message_
                yield message_
                complete_message += message_

        files = self.params.get("files", [])

        treat_file_path = files is not None and not isinstance(files, list) and isinstance(files, str)
        if treat_file_path:
            self.params["files"] = rewrite_file_path(files)

        if hasattr(self.params.get("sender_name"), "get_text"):
            sender_name = self.params.get("sender_name").get_text()
        else:
            sender_name = self.params.get("sender_name")
        self.artifacts = ChatOutputResponse(
            message=complete_message,
            sender=self.params.get("sender", ""),
            sender_name=sender_name,
            files=[{"path": file} if isinstance(file, str) else file for file in self.params.get("files", [])],
            type=ArtifactType.OBJECT.value,
        ).model_dump()

        message = await Message.create(
            text=complete_message,
            sender=self.params.get("sender", ""),
            sender_name=self.params.get("sender_name", ""),
            files=self.params.get("files", []),
            flow_id=self.graph.flow_id,
            session_id=self.params.get("session_id", ""),
        )
        self.params[INPUT_FIELD_NAME] = complete_message
        if isinstance(self.built_object, dict):
            for key, value in self.built_object.items():
                if hasattr(value, "text") and (isinstance(value.text, AsyncIterator | Iterator) or value.text == ""):
                    self.built_object[key] = message
        else:
            self.built_object = message
            self.artifacts_type = ArtifactType.MESSAGE

        self.finalize_build()
        await logger.adebug(f"Streamed message: {complete_message}")
        edges = self.get_edge_with_target(self.id)
        for edge in edges:
            origin_vertex = self.graph.get_vertex(edge.source_id)
            for key, value in origin_vertex.results.items():
                if isinstance(value, AsyncIterator | Iterator):
                    origin_vertex.results[key] = complete_message
        if (
            self.custom_component
            and hasattr(self.custom_component, "should_store_message")
            and hasattr(self.custom_component, "store_message")
        ):
            await self.custom_component.store_message(message)
        await log_vertex_build(
            flow_id=self.graph.flow_id,
            vertex_id=self.id,
            valid=True,
            params=self.built_object_repr(),
            data=self.result,
            artifacts=self.artifacts,
        )

        self._validate_built_object()
        self.built = True

    async def consume_async_generator(self) -> None:
        """消费异步生成器，触发流式处理完成。"""
        async for _ in self.stream():
            pass

    def _is_chat_input(self):
        """判断是否为 ChatInput 接口节点。"""
        return self.vertex_type == InterfaceComponentTypes.ChatInput and self.is_input


class StateVertex(ComponentVertex):
    """状态节点，负责保存状态型组件结果。"""
    def __init__(self, data: NodeData, graph):
        """初始化状态节点并设置构建步骤。"""
        super().__init__(data, graph=graph)
        self.steps = [self._build]
        self.is_state = True

    def built_object_repr(self):
        """优先返回工件中的展示文本。"""
        if self.artifacts and "repr" in self.artifacts:
            return self.artifacts["repr"] or super().built_object_repr()
        return None


def dict_to_codeblock(d: dict) -> str:
    """将字典序列化为 JSON 代码块字符串。"""
    serialized = {key: serialize(val) for key, val in d.items()}
    json_str = json.dumps(serialized, indent=4)
    return f"```json\n{json_str}\n```"
