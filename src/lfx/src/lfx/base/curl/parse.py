"""
模块名称：curl 命令解析器

本模块提供将 `curl` 命令解析为请求上下文的能力，主要用于把 CLI 输入转换为 Langflow 内部可消费的请求参数。主要功能包括：
- 解析 `-X/-d/-H/-u/-x` 等参数为结构化字段
- 抽取 `cookie` 头并生成 `dict` 形式的 Cookie
- 归一化行续写 `\\` 的多行命令

关键组件：
- `ParsedArgs`：原始参数抽取结果（近似 `curl` 级别）
- `ParsedContext`：请求级上下文（方法/头/代理/认证）
- `parse_curl_command` / `parse_context`：解析入口

设计背景：上游 `uncurl` 在头部/代理/续行等场景的解析不稳定，本实现保留兼容的同时做本地修补。
注意事项：本模块只做字符串解析，不发起网络请求；`verify` 字段沿用 `-k/--insecure` 的布尔值语义，调用方需自行映射到实际 TLS 校验开关。
"""

import re
import shlex
from collections import OrderedDict
from http.cookies import SimpleCookie
from typing import NamedTuple


class ParsedArgs(NamedTuple):
    """`curl` 原始参数的结构化抽取结果。

    契约：字段名与常见 `curl` 选项一一对应，`method` 默认 `get`，未出现的字段保持 `None`/空集合。
    失败语义：本类型不做校验；非法组合或解析失败由上层函数抛错。
    副作用：无。
    """

    command: str | None
    url: str | None
    data: str | None
    data_binary: str | None
    method: str
    headers: list[str]
    compressed: bool
    insecure: bool
    user: tuple[str, str]
    include: bool
    silent: bool
    proxy: str | None
    proxy_user: str | None
    cookies: dict[str, str]


class ParsedContext(NamedTuple):
    """请求级上下文模型，供上层构造请求使用。

    契约：`headers`/`cookies` 为已去除多余空白的字典；`auth` 为二元组或 `None`；`proxy` 为 `requests` 风格代理字典。
    失败语义：本类型不做校验；语义错误由调用方处理。
    副作用：无。
    """

    method: str
    url: str
    data: str | None
    headers: dict[str, str]
    cookies: dict[str, str]
    verify: bool
    auth: tuple[str, str] | None
    proxy: dict[str, str] | None


def normalize_newlines(multiline_text):
    """契约：将 `curl` 的续行格式 `\\` + 换行替换为单个空格，保持参数语义不变。"""

    return multiline_text.replace(" \\\n", " ")


def parse_curl_command(curl_command):
    """将单条 `curl` 命令解析为结构化参数，供后续上下文构建使用。

    契约：输入为完整 `curl` 字符串；输出 `ParsedArgs`，字段与选项保持一对一映射。
    失败语义：非法命令抛 `ValueError`；`shlex.split` 在引号不匹配时可能抛 `ValueError`。
    副作用：无（纯解析）。

    关键路径（三步）：
    1) 归一化换行并用 `shlex` 分词
    2) 扫描参数并填充模板字段
    3) 合并 `-X` 指定的方法并返回结果
    """

    tokens = shlex.split(normalize_newlines(curl_command))
    tokens = [token for token in tokens if token and token != " "]
    if tokens and "curl" not in tokens[0]:
        msg = "Invalid curl command"
        raise ValueError(msg)
    args_template = {
        "command": None,
        "url": None,
        "data": None,
        "data_binary": None,
        "method": "get",
        "headers": [],
        "compressed": False,
        "insecure": False,
        "user": (),
        "include": False,
        "silent": False,
        "proxy": None,
        "proxy_user": None,
        "cookies": {},
    }
    args = args_template.copy()
    method_on_curl = None
    i = 0
    while i < len(tokens):
        token = tokens[i]
        if token == "-X":
            i += 1
            args["method"] = tokens[i].lower()
            method_on_curl = tokens[i].lower()
        elif token in {"-d", "--data"}:
            i += 1
            args["data"] = tokens[i]
        elif token in {"-b", "--data-binary", "--data-raw"}:
            i += 1
            args["data_binary"] = tokens[i]
        elif token in {"-H", "--header"}:
            i += 1
            args["headers"].append(tokens[i])
        elif token == "--compressed":
            args["compressed"] = True
        elif token in {"-k", "--insecure"}:
            args["insecure"] = True
        elif token in {"-u", "--user"}:
            i += 1
            args["user"] = tuple(tokens[i].split(":"))
        elif token in {"-I", "--include"}:
            args["include"] = True
        elif token in {"-s", "--silent"}:
            args["silent"] = True
        elif token in {"-x", "--proxy"}:
            i += 1
            args["proxy"] = tokens[i]
        elif token in {"-U", "--proxy-user"}:
            i += 1
            args["proxy_user"] = tokens[i]
        elif not token.startswith("-"):
            if args["command"] is None:
                args["command"] = token
            else:
                args["url"] = token
        i += 1

    args["method"] = method_on_curl or args["method"]

    return ParsedArgs(**args)


def parse_context(curl_command):
    """将 `curl` 字符串转为请求上下文，供上层请求构造使用。

    契约：输出 `ParsedContext`，其中 `cookies` 来自 `cookie` 头解析，`proxy` 依据 `-x/-U` 组合生成。
    失败语义：非法命令抛 `ValueError`；头字段缺少 `:` 可能触发 `ValueError`；`-u` 解析异常会抛 `AttributeError`。
    副作用：无（仅构造数据结构）。

    关键路径（三步）：
    1) 解析命令并推断方法/数据体
    2) 规范化头部并提取 Cookie
    3) 组装认证与代理配置并返回
    """

    method = "get"
    if not curl_command or not curl_command.strip():
        return ParsedContext(
            method=method, url="", data=None, headers={}, cookies={}, verify=True, auth=None, proxy=None
        )

    curl_command = curl_command.strip()
    parsed_args: ParsedArgs = parse_curl_command(curl_command)

    post_data = getattr(parsed_args, "data", None) or getattr(parsed_args, "data_binary", None)
    if post_data:
        method = "post"

    if getattr(parsed_args, "method", None):
        method = parsed_args.method.lower()

    cookie_dict = OrderedDict()
    quoted_headers = OrderedDict()

    for curl_header in getattr(parsed_args, "headers", []):
        if curl_header.startswith(":"):
            occurrence = [m.start() for m in re.finditer(r":", curl_header)]
            header_key, header_value = curl_header[: occurrence[1]], curl_header[occurrence[1] + 1 :]
        else:
            header_key, header_value = curl_header.split(":", 1)

        if header_key.lower().strip("$") == "cookie":
            cookie = SimpleCookie(bytes(header_value, "ascii").decode("unicode-escape"))
            for key in cookie:
                cookie_dict[key] = cookie[key].value
        else:
            quoted_headers[header_key] = header_value.strip()

    user = getattr(parsed_args, "user", None)
    if user:
        user = tuple(user.split(":"))

    proxies = getattr(parsed_args, "proxy", None)
    if proxies and getattr(parsed_args, "proxy_user", None):
        proxies = {
            "http": f"http://{parsed_args.proxy_user}@{parsed_args.proxy}/",
            "https": f"http://{parsed_args.proxy_user}@{parsed_args.proxy}/",
        }
    elif proxies:
        proxies = {
            "http": f"http://{parsed_args.proxy}/",
            "https": f"http://{parsed_args.proxy}/",
        }

    return ParsedContext(
        method=method,
        url=getattr(parsed_args, "url", ""),
        data=post_data,
        headers=quoted_headers,
        cookies=cookie_dict,
        # 注意：此处 `verify` 直接等于 `-k/--insecure` 的布尔值，与常见请求库语义相反。
        verify=getattr(parsed_args, "insecure", True),
        auth=user,
        proxy=proxies,
    )
