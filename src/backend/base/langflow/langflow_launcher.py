"""模块名称：Langflow启动器

本模块提供跨平台启动Langflow应用的功能，主要用于处理macOS下的Objective-C运行时问题。
主要功能包括：
- 在macOS上设置必要的环境变量
- 替换当前进程以正确启动Langflow
- 在其他平台上直接启动Langflow

设计背景：由于macOS上的Objective-C库在Python运行时预加载，需要在进程启动前设置环境变量
注意事项：必须在Python代码执行前设置OBJC_DISABLE_INITIALIZE_FORK_SAFETY环境变量
"""

import os
import platform
import sys

import typer


def main():
    """启动Langflow并进行适当的环境设置

    在macOS上，设置所需环境变量并替换当前进程。
    在其他平台上，直接调用主函数。
    
    关键路径（三步）：
    1) 检测操作系统平台
    2) 根据平台选择合适的启动方式
    3) 执行启动过程
    
    异常流：在macOS上使用exec替换进程，在其他平台上直接调用langflow_main
    性能瓶颈：无显著性能瓶颈
    排障入口：无特定日志关键字
    """
    if platform.system() == "Darwin":  # macOS
        _launch_with_exec()
    else:
        # 在非macOS系统上，直接调用主函数
        from langflow.__main__ import main as langflow_main

        langflow_main()


def _launch_with_exec():
    """通过替换当前进程启动Langflow并配置适当的环境
    
    决策：使用execv替换当前进程而非subprocess
    问题：Objective-C库在Python运行时预加载，导致fork安全问题
    方案：在macOS上使用os.execv完全替换当前进程，预先设置环境变量
    代价：无法保持原始启动进程，必须在exec之前设置所有必要环境变量
    重评：当macOS Objective-C运行时行为改变时需要重新评估
    
    此方法是必要的，因为Objective-C库在Python运行时预加载，早于任何Python代码执行。
    在Python代码中设置OBJC_DISABLE_INITIALIZE_FORK_SAFETY为时已晚——必须在父进程环境中
    在生成Python之前设置。
    
    使用OBJC_PRINT_INITIALIZE=YES测试确认NSCheapMutableString和其他Objective-C类
    在Python启动期间初始化，早于任何用户代码运行。当gunicorn或多进程尝试fork进程时，
    会导致fork安全问题。
    
    exec方法设置环境变量，然后用新的Python进程替换当前进程。
    这比subprocess更高效，因为我们不需要保持启动进程运行，信号由目标进程直接处理。
    
    关键路径（三步）：
    1) 设置必要的环境变量
    2) 执行execv替换当前进程
    3) 处理可能的OSError异常
    
    异常流：如果exec失败，则输出错误信息并退出程序
    性能瓶颈：无显著性能瓶颈
    排障入口：失败时输出到stderr的错误信息
    """
    # 在exec之前设置环境变量
    os.environ["OBJC_DISABLE_INITIALIZE_FORK_SAFETY"] = "YES"
    # 额外修复gunicorn兼容性问题
    os.environ["no_proxy"] = "*"

    try:
        os.execv(sys.executable, [sys.executable, "-m", "langflow.__main__", *sys.argv[1:]])  # noqa: S606
    except OSError as e:
        # 如果exec失败，我们需要退出，因为进程替换失败了
        typer.echo(f"Failed to exec langflow: {e}", file=sys.stderr)
        sys.exit(1)