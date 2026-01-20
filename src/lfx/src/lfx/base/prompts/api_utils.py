"""
模块名称：提示词模板 `API` 工具

本模块提供提示词模板的变量校验与模板字段同步逻辑，主要用于服务端处理前端模板更新、
生成默认字段与保留历史字段值。
主要功能包括：
- 解析 `Mustache`/`f-string` 变量并校验合法性
- 修复变量名中的非法字符并汇总错误
- 维护前端模板字段与自定义字段列表

关键组件：
- `validate_prompt`
- `process_prompt_template`
- 变量校验与模板同步辅助函数

设计背景：前端模板编辑可产生不合法变量，需要统一校验与修复入口。
注意事项：非法变量将抛 `ValueError`，模板字段更新失败会抛 `HTTPException`。
"""

from collections import defaultdict
from typing import Any

from fastapi import HTTPException
from langchain_core.prompts import PromptTemplate
from langchain_core.prompts.string import mustache_template_vars

from lfx.inputs.inputs import DefaultPromptField
from lfx.interface.utils import extract_input_variables_from_prompt
from lfx.log.logger import logger

_INVALID_CHARACTERS = {
    " ",
    ",",
    ".",
    ":",
    ";",
    "!",
    "?",
    "/",
    "\\",
    "(",
    ")",
    "[",
    "]",
}

_INVALID_NAMES = {
    "code",
    "input_variables",
    "output_parser",
    "partial_variables",
    "template",
    "template_format",
    "validate_template",
}


def _is_json_like(var):
    """判断变量是否为 `JSON` 样式的 `Mustache` 包裹内容

    契约：
    - 输入：变量字符串
    - 输出：布尔值
    - 副作用：无
    - 失败语义：无
    """
    if var.startswith("{{") and var.endswith("}}"):
        # 注意：双花括号变量不校验其内部内容
        return True
    # 注意：`JSON` 字符串可能包含换行与缩进
    # 注意：缩进/换行会引入 `\n` 或空格，需要先清理
    # 注意：测试样例：`'\n{{\n    \"test\": \"hello\",\n    \"text\": \"world\"\n}}\n'`
    # 注意：清理首尾空白后再判断 `{{` 与 `}}`
    var = var.strip()
    var = var.replace("\n", "")
    var = var.replace(" ", "")
    # 注意：清理后再判断是否为 `JSON` 样式
    return var.startswith("{{") and var.endswith("}}")


def _fix_variable(var, invalid_chars, wrong_variables):
    """清理变量名中的非法字符

    契约：
    - 输入：变量名、非法字符列表、错误变量列表
    - 输出：清理后的变量名与更新后的列表
    - 副作用：更新 `invalid_chars` 与 `wrong_variables`
    - 失败语义：空字符串直接返回
    """
    if not var:
        return var, invalid_chars, wrong_variables
    new_var = var

    # 注意：变量名以数字开头视为非法
    if var[0].isdigit():
        invalid_chars.append(var[0])
        new_var, invalid_chars, wrong_variables = _fix_variable(var[1:], invalid_chars, wrong_variables)

    # 注意：临时替换 `{{`/`}}`，避免误判为非法字符
    new_var = new_var.replace("{{", "ᴛᴇᴍᴘᴏᴘᴇɴ").replace("}}", "ᴛᴇᴍᴘᴄʟᴏsᴇ")  # noqa: RUF001

    # 移除非法字符
    for char in new_var:
        if char in _INVALID_CHARACTERS:
            invalid_chars.append(char)
            new_var = new_var.replace(char, "")
            if var not in wrong_variables:  # Avoid duplicating entries
                wrong_variables.append(var)

    # 注意：恢复 `{{`/`}}`
    new_var = new_var.replace("ᴛᴇᴍᴘᴏᴘᴇɴ", "{{").replace("ᴛᴇᴍᴘᴄʟᴏsᴇ", "}}")  # noqa: RUF001

    return new_var, invalid_chars, wrong_variables


def _check_variable(var, invalid_chars, wrong_variables, empty_variables):
    """检查变量是否包含非法字符或为空

    契约：
    - 输入：变量名与各类问题列表
    - 输出：更新后的错误/空变量列表
    - 副作用：可能追加到 `wrong_variables`/`empty_variables`
    - 失败语义：无
    """
    if any(char in invalid_chars for char in var):
        wrong_variables.append(var)
    elif var == "":
        empty_variables.append(var)
    return wrong_variables, empty_variables


def _check_for_errors(input_variables, fixed_variables, wrong_variables, empty_variables) -> None:
    """检查变量校验结果并抛错

    契约：
    - 输入：原始变量、修正变量、错误与空变量列表
    - 输出：无
    - 副作用：校验失败时抛出 `ValueError`
    - 失败语义：存在非法变量时抛 `ValueError`
    """
    if any(var for var in input_variables if var not in fixed_variables):
        error_message = (
            f"Input variables contain invalid characters or formats. \n"
            f"Invalid variables: {', '.join(wrong_variables)}.\n"
            f"Empty variables: {', '.join(empty_variables)}. \n"
            f"Fixed variables: {', '.join(fixed_variables)}."
        )
        raise ValueError(error_message)


def _check_input_variables(input_variables):
    """校验并修正输入变量列表

    契约：
    - 输入：变量名列表
    - 输出：修正后的变量名列表
    - 副作用：可能抛出 `ValueError`
    - 失败语义：变量非法时抛 `ValueError`
    """
    invalid_chars = []
    fixed_variables = []
    wrong_variables = []
    empty_variables = []
    variables_to_check = []

    for var in input_variables:
        # 注意：若变量是 `JSON` 样式，则跳过校验
        if _is_json_like(var):
            continue

        new_var, wrong_variables, empty_variables = _fix_variable(var, invalid_chars, wrong_variables)
        wrong_variables, empty_variables = _check_variable(var, _INVALID_CHARACTERS, wrong_variables, empty_variables)
        fixed_variables.append(new_var)
        variables_to_check.append(var)

    _check_for_errors(variables_to_check, fixed_variables, wrong_variables, empty_variables)

    return fixed_variables


def validate_prompt(prompt_template: str, *, silent_errors: bool = False, is_mustache: bool = False) -> list[str]:
    """校验提示词模板并返回变量列表

    关键路径（三步）：
    1) 解析 `Mustache`/`f-string` 变量
    2) 校验变量合法性并过滤保留名
    3) 构造 `PromptTemplate` 验证语法

    异常流：模板语法或变量非法时抛 `ValueError`。
    性能瓶颈：模板变量较多时。
    排障入口：异常消息包含具体变量或解析错误。
    
    契约：
    - 输入：模板字符串与解析模式
    - 输出：变量列表
    - 副作用：可能记录异常日志
    - 失败语义：非法模板抛 `ValueError`
    """
    if is_mustache:
        # 仅提取 `Mustache` 变量
        try:
            input_variables = mustache_template_vars(prompt_template)
        except Exception as exc:
            # 注意：`Mustache` 解析错误信息较晦涩（如 `unclosed tag at line 1`）
            # 注意：提供更友好的错误提示
            error_str = str(exc).lower()
            if "unclosed" in error_str or "tag" in error_str:
                msg = "Invalid template syntax. Check that all {{variables}} have matching opening and closing braces."
            else:
                msg = f"Invalid mustache template: {exc}"
            raise ValueError(msg) from exc

        # 同时获取 `f-string` 变量用于过滤
        fstring_vars = extract_input_variables_from_prompt(prompt_template)

        # 仅保留 `Mustache` 语法变量（排除 `f-string` 语法）
        # 注意：处理模板同时包含 `{var}` 与 `{{var}}` 的情况
        input_variables = [v for v in input_variables if v not in fstring_vars or f"{{{{{v}}}}}" in prompt_template]
    else:
        # 提取 `f-string` 变量
        input_variables = extract_input_variables_from_prompt(prompt_template)

        # 同时获取 `Mustache` 变量用于过滤
        mustache_vars = mustache_template_vars(prompt_template)

        # 仅保留非 `Mustache` 语法变量
        # 注意：处理模板同时包含 `{var}` 与 `{{var}}` 的情况
        input_variables = [v for v in input_variables if v not in mustache_vars]

    # 校验变量是否包含非法字符
    input_variables = _check_input_variables(input_variables)
    if any(var in _INVALID_NAMES for var in input_variables):
        msg = f"Invalid input variables. None of the variables can be named {', '.join(input_variables)}. "
        raise ValueError(msg)

    try:
        PromptTemplate(template=prompt_template, input_variables=input_variables)
    except Exception as exc:
        msg = f"Invalid prompt: {exc}"
        logger.exception(msg)
        if not silent_errors:
            raise ValueError(msg) from exc

    return input_variables


def get_old_custom_fields(custom_fields, name):
    """获取旧的自定义字段列表并清空当前字段

    契约：
    - 输入：自定义字段映射与名称
    - 输出：旧字段列表
    - 副作用：清空 `custom_fields[name]`
    - 失败语义：字段不存在时返回空列表
    """
    try:
        if len(custom_fields) == 1 and name == "":
            # 注意：仅有一个字段且名称为空时，视为节点创建后的首次请求
            name = next(iter(custom_fields.keys()))

        old_custom_fields = custom_fields[name]
        if not old_custom_fields:
            old_custom_fields = []

        old_custom_fields = old_custom_fields.copy()
    except KeyError:
        old_custom_fields = []
    custom_fields[name] = []
    return old_custom_fields


def add_new_variables_to_template(input_variables, custom_fields, template, name) -> None:
    """将新变量写入模板并同步自定义字段

    契约：
    - 输入：变量列表、自定义字段、模板与名称
    - 输出：无
    - 副作用：更新模板与自定义字段
    - 失败语义：模板更新失败抛 `HTTPException`
    """
    for variable in input_variables:
        try:
            template_field = DefaultPromptField(name=variable, display_name=variable)
            if variable in template:
                # 注意：保留旧字段值
                template_field.value = template[variable]["value"]

            template[variable] = template_field.to_dict()

            # 注意：避免重复追加变量名
            if variable not in custom_fields[name]:
                custom_fields[name].append(variable)

        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc


def remove_old_variables_from_template(old_custom_fields, input_variables, custom_fields, template, name) -> None:
    """从模板与字段中移除已不存在的变量

    契约：
    - 输入：旧字段、当前变量、自定义字段、模板与名称
    - 输出：无
    - 副作用：移除字段与模板键
    - 失败语义：模板更新失败抛 `HTTPException`
    """
    for variable in old_custom_fields:
        if variable not in input_variables:
            try:
                # 注意：从 `custom_fields[name]` 中移除变量
                if variable in custom_fields[name]:
                    custom_fields[name].remove(variable)

                # 注意：从模板中移除变量
                template.pop(variable, None)

            except Exception as exc:
                raise HTTPException(status_code=500, detail=str(exc)) from exc


def update_input_variables_field(input_variables, template) -> None:
    """更新模板中的 `input_variables` 字段

    契约：
    - 输入：变量列表与模板
    - 输出：无
    - 副作用：更新模板字段
    - 失败语义：无
    """
    if "input_variables" in template:
        template["input_variables"]["value"] = input_variables


def process_prompt_template(
    template: str,
    name: str,
    custom_fields: dict[str, list[str]] | None,
    frontend_node_template: dict[str, Any],
    *,
    is_mustache: bool = False,
):
    """处理并校验提示词模板，更新模板与自定义字段

    关键路径（三步）：
    1) 校验模板并提取变量
    2) 同步新增/删除变量到模板
    3) 更新 `input_variables` 字段

    异常流：变量非法或模板更新失败时抛异常。
    性能瓶颈：模板变量规模较大时。
    排障入口：异常消息包含变量与模板信息。
    
    契约：
    - 输入：模板字符串、名称、自定义字段与前端模板
    - 输出：变量列表
    - 副作用：更新模板与自定义字段
    - 失败语义：校验或更新失败时抛异常
    """
    # 校验模板并提取变量
    input_variables = validate_prompt(template, is_mustache=is_mustache)

    # 注意：`custom_fields` 为空则初始化
    if custom_fields is None:
        custom_fields = defaultdict(list)

    # 获取旧字段列表
    old_custom_fields = get_old_custom_fields(custom_fields, name)

    # 添加新变量到模板
    add_new_variables_to_template(input_variables, custom_fields, frontend_node_template, name)

    # 移除旧变量
    remove_old_variables_from_template(old_custom_fields, input_variables, custom_fields, frontend_node_template, name)

    # 更新模板中的 `input_variables` 字段
    update_input_variables_field(input_variables, frontend_node_template)

    return input_variables
