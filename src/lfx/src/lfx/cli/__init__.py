"""
模块名称：LFX CLI 包入口

本模块提供 CLI 子命令的懒加载入口，主要用于减少导入开销并避免不必要的启动依赖。主要功能包括：
- 按需暴露 `serve_command` 命令入口

关键组件：
- `__getattr__`：按需导入 `lfx.cli.commands.serve_command`

设计背景：CLI 启动路径需要尽量轻量，避免在未使用的命令上引入依赖和副作用。
注意事项：仅暴露 `serve_command`，其他属性访问会抛 `AttributeError`。
"""

__all__ = ["serve_command"]


def __getattr__(name: str):
    """按需返回 CLI 命令入口。

    契约：仅支持 `serve_command`；其他属性访问抛 `AttributeError`。
    失败语义：未知属性直接抛 `AttributeError`，用于阻断误用。
    副作用：首次访问时会触发 `lfx.cli.commands` 导入。
    """
    if name == "serve_command":
        from lfx.cli.commands import serve_command

        return serve_command
    msg = f"module {__name__!r} has no attribute {name!r}"
    raise AttributeError(msg)
