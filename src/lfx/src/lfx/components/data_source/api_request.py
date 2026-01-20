"""
模块名称：`API` 请求组件

本模块提供基于 `URL` 或 `cURL` 的 `HTTP` 请求能力，支持参数解析、`SSRF` 防护与结果保存。
主要功能包括：
- 解析 `cURL` 并填充请求字段
- 处理请求头/参数/请求体
- 发送请求并返回结构化 `Data`

关键组件：
- `APIRequestComponent`

设计背景：提供通用的 `HTTP` 请求入口以接入外部数据源。
注意事项：重定向可能引入 `SSRF` 风险；`save_to_file` 会写入临时目录。
"""

import json
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

import aiofiles
import aiofiles.os as aiofiles_os
import httpx
import validators

from lfx.base.curl.parse import parse_context
from lfx.custom.custom_component.component import Component
from lfx.inputs.inputs import TabInput
from lfx.io import (
    BoolInput,
    DataInput,
    DropdownInput,
    IntInput,
    MessageTextInput,
    MultilineInput,
    Output,
    TableInput,
)
from lfx.schema.data import Data
from lfx.schema.dotdict import dotdict
from lfx.utils.component_utils import set_current_fields, set_field_advanced, set_field_display
from lfx.utils.ssrf_protection import SSRFProtectionError, validate_url_for_ssrf

# 模式对应字段
MODE_FIELDS = {
    "URL": [
        "url_input",
        "method",
    ],
    "cURL": ["curl_input"],
}

# 始终可见的字段
DEFAULT_FIELDS = ["mode"]


class APIRequestComponent(Component):
    """`API` 请求组件

    契约：
    - 输入：`URL`/`cURL`、请求方法、参数、请求头、请求体等
    - 输出：`Data`（包含响应与元信息）
    - 副作用：发起外部请求并更新 `self.status`
    - 失败语义：请求或解析失败时抛 `ValueError`
    """
    display_name = "API Request"
    description = "Make HTTP requests using URL or cURL commands."
    documentation: str = "https://docs.langflow.org/api-request"
    icon = "Globe"
    name = "APIRequest"

    inputs = [
        MessageTextInput(
            name="url_input",
            display_name="URL",
            info="Enter the URL for the request.",
            advanced=False,
            tool_mode=True,
        ),
        MultilineInput(
            name="curl_input",
            display_name="cURL",
            info=(
                "Paste a curl command to populate the fields. "
                "This will fill in the dictionary fields for headers and body."
            ),
            real_time_refresh=True,
            tool_mode=True,
            advanced=True,
            show=False,
        ),
        DropdownInput(
            name="method",
            display_name="Method",
            options=["GET", "POST", "PATCH", "PUT", "DELETE"],
            value="GET",
            info="The HTTP method to use.",
            real_time_refresh=True,
        ),
        TabInput(
            name="mode",
            display_name="Mode",
            options=["URL", "cURL"],
            value="URL",
            info="Enable cURL mode to populate fields from a cURL command.",
            real_time_refresh=True,
        ),
        DataInput(
            name="query_params",
            display_name="Query Parameters",
            info="The query parameters to append to the URL.",
            advanced=True,
        ),
        TableInput(
            name="body",
            display_name="Body",
            info="The body to send with the request as a dictionary (for POST, PATCH, PUT).",
            table_schema=[
                {
                    "name": "key",
                    "display_name": "Key",
                    "type": "str",
                    "description": "Parameter name",
                },
                {
                    "name": "value",
                    "display_name": "Value",
                    "description": "Parameter value",
                },
            ],
            value=[],
            input_types=["Data"],
            advanced=True,
            real_time_refresh=True,
        ),
        TableInput(
            name="headers",
            display_name="Headers",
            info="The headers to send with the request",
            table_schema=[
                {
                    "name": "key",
                    "display_name": "Header",
                    "type": "str",
                    "description": "Header name",
                },
                {
                    "name": "value",
                    "display_name": "Value",
                    "type": "str",
                    "description": "Header value",
                },
            ],
            value=[{"key": "User-Agent", "value": "Langflow/1.0"}],
            advanced=True,
            input_types=["Data"],
            real_time_refresh=True,
        ),
        IntInput(
            name="timeout",
            display_name="Timeout",
            value=30,
            info="The timeout to use for the request.",
            advanced=True,
        ),
        BoolInput(
            name="follow_redirects",
            display_name="Follow Redirects",
            value=False,
            info=(
                "Whether to follow HTTP redirects. "
                "WARNING: Enabling redirects may allow SSRF bypass attacks where a public URL "
                "redirects to internal resources. Only enable if you trust the target server. "
                "See OWASP SSRF Prevention Cheat Sheet for details."
            ),
            advanced=True,
        ),
        BoolInput(
            name="save_to_file",
            display_name="Save to File",
            value=False,
            info="Save the API response to a temporary file",
            advanced=True,
        ),
        BoolInput(
            name="include_httpx_metadata",
            display_name="Include HTTPx Metadata",
            value=False,
            info=(
                "Include properties such as headers, status_code, response_headers, "
                "and redirection_history in the output."
            ),
            advanced=True,
        ),
    ]

    outputs = [
        Output(display_name="API Response", name="data", method="make_api_request"),
    ]

    def _parse_json_value(self, value: Any) -> Any:
        """解析可能为 `JSON` 的字符串

        契约：
        - 输入：任意值
        - 输出：解析后的对象或原值
        - 副作用：无
        - 失败语义：解析失败时返回原值
        """
        if not isinstance(value, str):
            return value

        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return value
        else:
            return parsed

    def _process_body(self, body: Any) -> dict:
        """处理请求体并返回字典

        契约：
        - 输入：请求体（字典/字符串/列表）
        - 输出：字典
        - 副作用：无
        - 失败语义：无法处理时返回空字典
        """
        if body is None:
            return {}
        if hasattr(body, "data"):
            body = body.data
        if isinstance(body, dict):
            return self._process_dict_body(body)
        if isinstance(body, str):
            return self._process_string_body(body)
        if isinstance(body, list):
            return self._process_list_body(body)
        return {}

    def _process_dict_body(self, body: dict) -> dict:
        """解析字典请求体并转换内部 `JSON` 值

        契约：
        - 输入：字典
        - 输出：处理后的字典
        - 副作用：无
        - 失败语义：无
        """
        return {k: self._parse_json_value(v) for k, v in body.items()}

    def _process_string_body(self, body: str) -> dict:
        """解析字符串请求体

        契约：
        - 输入：字符串
        - 输出：字典（解析成功）或包含原始字符串的数据
        - 副作用：无
        - 失败语义：解析失败时返回 `{"data": body}`
        """
        try:
            return self._process_body(json.loads(body))
        except json.JSONDecodeError:
            return {"data": body}

    def _process_list_body(self, body: list) -> dict:
        """将列表请求体转换为键值字典

        契约：
        - 输入：列表
        - 输出：字典
        - 副作用：无
        - 失败语义：解析失败时返回空字典
        """
        processed_dict = {}
        try:
            for item in body:
                # 注意：解包 `Data` 对象
                current_item = item
                if hasattr(item, "data"):
                    unwrapped_data = item.data
                    # 注意：解包后为字典且非键值格式时直接使用
                    if isinstance(unwrapped_data, dict) and not self._is_valid_key_value_item(unwrapped_data):
                        return unwrapped_data
                    current_item = unwrapped_data
                if not self._is_valid_key_value_item(current_item):
                    continue
                key = current_item["key"]
                value = self._parse_json_value(current_item["value"])
                processed_dict[key] = value
        except (KeyError, TypeError, ValueError) as e:
            self.log(f"Failed to process body list: {e}")
            return {}
        return processed_dict

    def _is_valid_key_value_item(self, item: Any) -> bool:
        """判断条目是否为合法键值结构

        契约：
        - 输入：任意对象
        - 输出：`bool`
        - 副作用：无
        - 失败语义：无
        """
        return isinstance(item, dict) and "key" in item and "value" in item

    def parse_curl(self, curl: str, build_config: dotdict) -> dotdict:
        """解析 `cURL` 并更新构建配置

        关键路径（三步）：
        1) 解析 `cURL` 上下文
        2) 填充请求方法/URL/头与请求体
        3) 返回更新后的配置

        异常流：解析失败抛 `ValueError`。
        性能瓶颈：无显著性能瓶颈。
        排障入口：日志与异常信息。
        
        契约：
        - 输入：`cURL` 字符串与配置对象
        - 输出：更新后的配置对象
        - 副作用：无
        - 失败语义：解析失败时抛 `ValueError`
        """
        try:
            parsed = parse_context(curl)

            # 更新基础配置
            url = parsed.url
            # 注意：写入前先规范化 `URL`
            url = self._normalize_url(url)

            build_config["url_input"]["value"] = url
            build_config["method"]["value"] = parsed.method.upper()

            # 处理请求头
            headers_list = [{"key": k, "value": v} for k, v in parsed.headers.items()]
            build_config["headers"]["value"] = headers_list

            # 处理请求体
            if not parsed.data:
                build_config["body"]["value"] = []
            elif parsed.data:
                try:
                    json_data = json.loads(parsed.data)
                    if isinstance(json_data, dict):
                        body_list = [
                            {"key": k, "value": json.dumps(v) if isinstance(v, dict | list) else str(v)}
                            for k, v in json_data.items()
                        ]
                        build_config["body"]["value"] = body_list
                    else:
                        build_config["body"]["value"] = [{"key": "data", "value": json.dumps(json_data)}]
                except json.JSONDecodeError:
                    build_config["body"]["value"] = [{"key": "data", "value": parsed.data}]

        except Exception as exc:
            msg = f"Error parsing curl: {exc}"
            self.log(msg)
            raise ValueError(msg) from exc

        return build_config

    def _normalize_url(self, url: str) -> str:
        """规范化 `URL`，缺省协议时添加 `https://`

        契约：
        - 输入：`URL` 字符串
        - 输出：规范化后的 `URL`
        - 副作用：无
        - 失败语义：空 `URL` 时抛 `ValueError`
        """
        if not url or not isinstance(url, str):
            msg = "URL cannot be empty"
            raise ValueError(msg)

        url = url.strip()
        if url.startswith(("http://", "https://")):
            return url
        return f"https://{url}"

    async def make_request(
        self,
        client: httpx.AsyncClient,
        method: str,
        url: str,
        headers: dict | None = None,
        body: Any = None,
        timeout: int = 5,
        *,
        follow_redirects: bool = True,
        save_to_file: bool = False,
        include_httpx_metadata: bool = False,
    ) -> Data:
        """执行 `HTTP` 请求并返回结构化结果

        关键路径（三步）：
        1) 校验方法与处理请求体
        2) 发送请求并收集重定向历史
        3) 解析响应并生成 `Data`

        异常流：`httpx` 异常时返回包含错误的 `Data`。
        性能瓶颈：网络请求与响应解析。
        排障入口：返回数据中的 `error` 字段。
        
        契约：
        - 输入：请求参数与 `httpx` 客户端
        - 输出：`Data`
        - 副作用：可能写入临时文件
        - 失败语义：请求异常时返回错误 `Data`
        """
        method = method.upper()
        if method not in {"GET", "POST", "PATCH", "PUT", "DELETE"}:
            msg = f"Unsupported method: {method}"
            raise ValueError(msg)

        processed_body = self._process_body(body)
        redirection_history = []

        try:
            # 组装请求参数
            request_params = {
                "method": method,
                "url": url,
                "headers": headers,
                "json": processed_body,
                "timeout": timeout,
                "follow_redirects": follow_redirects,
            }
            response = await client.request(**request_params)

            redirection_history = [
                {
                    "url": redirect.headers.get("Location", str(redirect.url)),
                    "status_code": redirect.status_code,
                }
                for redirect in response.history
            ]

            is_binary, file_path = await self._response_info(response, with_file_path=save_to_file)
            response_headers = self._headers_to_dict(response.headers)

            # 基础元信息
            metadata = {
                "source": url,
                "status_code": response.status_code,
                "response_headers": response_headers,
            }

            if redirection_history:
                metadata["redirection_history"] = redirection_history

            if save_to_file:
                mode = "wb" if is_binary else "w"
                encoding = response.encoding if mode == "w" else None
                if file_path:
                    await aiofiles_os.makedirs(file_path.parent, exist_ok=True)
                    if is_binary:
                        async with aiofiles.open(file_path, "wb") as f:
                            await f.write(response.content)
                            await f.flush()
                    else:
                        async with aiofiles.open(file_path, "w", encoding=encoding) as f:
                            await f.write(response.text)
                            await f.flush()
                    metadata["file_path"] = str(file_path)

                if include_httpx_metadata:
                    metadata.update({"headers": headers})
                return Data(data=metadata)

            # 处理响应内容
            if is_binary:
                result = response.content
            else:
                try:
                    result = response.json()
                except json.JSONDecodeError:
                    self.log("Failed to decode JSON response")
                    result = response.text.encode("utf-8")

            metadata["result"] = result

            if include_httpx_metadata:
                metadata.update({"headers": headers})

            return Data(data=metadata)
        except (httpx.HTTPError, httpx.RequestError, httpx.TimeoutException) as exc:
            self.log(f"Error making request to {url}")
            return Data(
                data={
                    "source": url,
                    "headers": headers,
                    "status_code": 500,
                    "error": str(exc),
                    **({"redirection_history": redirection_history} if redirection_history else {}),
                },
            )

    def add_query_params(self, url: str, params: dict) -> str:
        """追加查询参数到 `URL`

        契约：
        - 输入：`URL` 与参数字典
        - 输出：新 `URL`
        - 副作用：无
        - 失败语义：无
        """
        if not params:
            return url
        url_parts = list(urlparse(url))
        query = dict(parse_qsl(url_parts[4]))
        query.update(params)
        url_parts[4] = urlencode(query)
        return urlunparse(url_parts)

    def _headers_to_dict(self, headers: httpx.Headers) -> dict[str, str]:
        """将 `HTTP` 请求头转换为字典并统一小写键

        契约：
        - 输入：`httpx.Headers`
        - 输出：字典
        - 副作用：无
        - 失败语义：无
        """
        return {k.lower(): v for k, v in headers.items()}

    def _process_headers(self, headers: Any) -> dict:
        """处理请求头输入并返回字典

        契约：
        - 输入：请求头（字典或列表）
        - 输出：字典
        - 副作用：无
        - 失败语义：无法处理时返回空字典
        """
        if headers is None:
            return {}
        if isinstance(headers, dict):
            return headers
        if isinstance(headers, list):
            return {item["key"]: item["value"] for item in headers if self._is_valid_key_value_item(item)}
        return {}

    async def make_api_request(self) -> Data:
        """执行 `HTTP` 请求并返回 `Data`

        关键路径（三步）：
        1) 规范化 `URL` 并进行 `SSRF` 校验
        2) 处理查询参数/请求头/请求体
        3) 调用 `make_request` 并返回结果

        异常流：`URL` 无效或 `SSRF` 校验失败时抛 `ValueError`。
        性能瓶颈：网络请求。
        排障入口：日志与异常信息。
        
        契约：
        - 输入：无（使用组件字段）
        - 输出：`Data`
        - 副作用：更新 `self.status`
        - 失败语义：校验或请求失败时抛 `ValueError`
        """
        method = self.method
        url = self.url_input.strip() if isinstance(self.url_input, str) else ""
        headers = self.headers or {}
        body = self.body or {}
        timeout = self.timeout
        follow_redirects = self.follow_redirects
        save_to_file = self.save_to_file
        include_httpx_metadata = self.include_httpx_metadata

        # 注意：重定向开启时输出安全提示
        if follow_redirects:
            self.log(
                "Security Warning: HTTP redirects are enabled. This may allow SSRF bypass attacks "
                "where a public URL redirects to internal resources (e.g., cloud metadata endpoints). "
                "Only enable this if you trust the target server."
            )

        # 注意：如需基于 `cURL` 自动回填字段，可在此处启用解析逻辑

        # 注意：校验前先规范化 `URL`
        url = self._normalize_url(url)

        # 校验 `URL` 结构
        if not validators.url(url):
            msg = f"Invalid URL provided: {url}"
            raise ValueError(msg)

        # `SSRF` 防护：验证 `URL` 是否指向内部资源
        # 注意：`TODO` 下一主版本移除 `warn_only=True` 以强制拦截
        try:
            validate_url_for_ssrf(url, warn_only=True)
        except SSRFProtectionError as e:
            # 注意：仅在 `warn_only=False` 时抛出
            msg = f"SSRF Protection: {e}"
            raise ValueError(msg) from e

        # 处理查询参数
        if isinstance(self.query_params, str):
            query_params = dict(parse_qsl(self.query_params))
        else:
            query_params = self.query_params.data if self.query_params else {}

        # 处理请求头与请求体
        headers = self._process_headers(headers)
        body = self._process_body(body)
        url = self.add_query_params(url, query_params)

        async with httpx.AsyncClient() as client:
            result = await self.make_request(
                client,
                method,
                url,
                headers,
                body,
                timeout,
                follow_redirects=follow_redirects,
                save_to_file=save_to_file,
                include_httpx_metadata=include_httpx_metadata,
            )
        self.status = result
        return result

    def update_build_config(self, build_config: dotdict, field_value: Any, field_name: str | None = None) -> dotdict:
        """根据模式更新构建配置

        契约：
        - 输入：构建配置、字段值与字段名
        - 输出：更新后的配置
        - 副作用：可能解析 `cURL` 并修改字段
        - 失败语义：解析失败时记录日志但不抛异常
        """
        if field_name != "mode":
            if field_name == "curl_input" and self.mode == "cURL" and self.curl_input:
                return self.parse_curl(self.curl_input, build_config)
            return build_config

        if field_value == "cURL":
            set_field_display(build_config, "curl_input", value=True)
            if build_config["curl_input"]["value"]:
                try:
                    build_config = self.parse_curl(build_config["curl_input"]["value"], build_config)
                except ValueError as e:
                    self.log(f"Failed to parse cURL input: {e}")
        else:
            set_field_display(build_config, "curl_input", value=False)

        return set_current_fields(
            build_config=build_config,
            action_fields=MODE_FIELDS,
            selected_action=field_value,
            default_fields=DEFAULT_FIELDS,
            func=set_field_advanced,
            default_value=True,
        )

    async def _response_info(
        self, response: httpx.Response, *, with_file_path: bool = False
    ) -> tuple[bool, Path | None]:
        """判断响应是否为二进制并生成保存路径

        契约：
        - 输入：`httpx.Response` 与是否生成文件路径
        - 输出：`(is_binary, file_path)`
        - 副作用：可能创建临时目录
        - 失败语义：无
        """
        content_type = response.headers.get("Content-Type", "")
        is_binary = "application/octet-stream" in content_type or "application/binary" in content_type

        if not with_file_path:
            return is_binary, None

        component_temp_dir = Path(tempfile.gettempdir()) / self.__class__.__name__

        # 异步创建目录
        await aiofiles_os.makedirs(component_temp_dir, exist_ok=True)

        filename = None
        if "Content-Disposition" in response.headers:
            content_disposition = response.headers["Content-Disposition"]
            filename_match = re.search(r'filename="(.+?)"', content_disposition)
            if filename_match:
                extracted_filename = filename_match.group(1)
                filename = extracted_filename

        # 推断文件名与扩展名
        if not filename:
            # 提取 `URL` 路径最后一段
            url_path = urlparse(str(response.request.url) if response.request else "").path
            base_name = Path(url_path).name  # Get the last segment of the path
            if not base_name:  # 路径为空或以 `/` 结尾
                base_name = "response"

            # 推断扩展名
            content_type_to_extension = {
                "text/plain": ".txt",
                "application/json": ".json",
                "image/jpeg": ".jpg",
                "image/png": ".png",
                "application/octet-stream": ".bin",
            }
            extension = content_type_to_extension.get(content_type, ".bin" if is_binary else ".txt")
            filename = f"{base_name}{extension}"

        # 生成完整文件路径
        file_path = component_temp_dir / filename

        # 异步检查文件是否存在并处理冲突
        try:
            # 使用 `x` 模式创建以检测冲突
            async with aiofiles.open(file_path, "x") as _:
                pass  # 文件创建成功，可直接使用
        except FileExistsError:
            # 文件存在则追加时间戳
            timestamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S%f")
            file_path = component_temp_dir / f"{timestamp}-{filename}"

        return is_binary, file_path
