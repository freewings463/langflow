"""
模块名称：`CLI` 进度指示器

本模块为 `Langflow` `CLI` 提供初始化/关闭流程的步骤化进度展示，面向终端用户提示当前阶段与耗时。主要功能包括：
- 提供 `ProgressIndicator` 管理步骤状态、动画与完成标记
- 预置初始化与关闭步骤（`create_langflow_progress` / `create_langflow_shutdown_progress`）

关键组件：
- `ProgressIndicator`: 维护步骤列表、动画线程与输出格式
- `create_langflow_progress`: 按启动顺序注册步骤
- `create_langflow_shutdown_progress`: 按关闭顺序注册步骤，支持多进程标识

设计背景：`CLI` 启动/关闭时间较长，缺少可视化反馈易被误判为卡死。
注意事项：输出直接写入 `stdout`，在非交互终端可能出现行覆盖不生效；`Windows` 使用 `ASCII` 以避免编码乱码。
"""

import platform
import sys
import threading
import time
from collections.abc import Generator
from contextlib import contextmanager
from typing import Any

import click

MIN_DURATION_THRESHOLD = 0.1  # 注意：仅在 `verbose` 且耗时 >100ms 时显示，避免短步骤噪音


class ProgressIndicator:
    """面向 `CLI` 的步骤进度指示器，负责动画、状态与结果输出。

    契约：通过 `add_step` 追加步骤，`start_step/complete_step` 控制状态；无返回值。
    副作用：写入 `stdout`，启动后台线程做动画，更新内部 `steps` 状态。
    失败语义：索引越界直接返回；线程 `join` 超时不抛错但可能残留光标覆盖。
    关键路径：1) 维护步骤状态 2) 动画线程刷新行 3) 完成时输出结果/耗时。
    决策：以线程而非异步循环驱动动画
    问题：`CLI` 启动流程为同步链路，需要最小侵入地展示动画
    方案：使用 `daemon` 线程循环刷新并通过 `_stop_animation` 控制
    代价：线程调度存在轻微抖动，`stdout` 非交互时可能无动画
    重评：当 `CLI` 主流程迁移到异步或需更低 `CPU` 占用时评估替代方案
    """

    def __init__(self, *, verbose: bool = False):
        """初始化进度指示器并配置平台相关符号集。

        契约：`verbose` 决定是否输出耗时与错误细节；返回 `None`。
        副作用：初始化内部状态但不启动动画线程。
        失败语义：`platform.system()` 异常会向上抛出。
        关键路径：1) 设定状态字段 2) 根据平台选择符号集 3) 初始化动画索引。
        决策：初始化阶段就固定符号集
        问题：运行中切换平台/终端配置不具备稳定检测点
        方案：以 `platform.system()` 的结果一次性决策
        代价：终端编码在运行中变化时无法自动适配
        重评：若后续需要动态切换主题/终端能力时再拆分策略
        """
        self.verbose = verbose
        self.steps: list[dict[str, Any]] = []
        self.current_step = 0
        self.running = False
        self._stop_animation = False
        self._animation_thread: threading.Thread | None = None

        # 决策：`Windows` 使用 `ASCII` 动画符号
        # 问题：部分 `Windows` 控制台默认编码无法稳定显示 `Unicode` 方块/勾叉
        # 方案：在 `Windows` 上固定 `ASCII` 旋转符号与图标
        # 代价：视觉辨识度降低
        # 重评：当默认终端支持 `UTF-8` 且测试通过时
        if platform.system() == "Windows":
            self._animation_chars = ["-", "\\", "|", "/"]  # `ASCII` 旋转符号
            self._success_icon = "+"  # `ASCII` 加号
            self._failure_icon = "x"  # `ASCII` 字母 x
            self._farewell_emoji = ":)"  # `ASCII` 笑脸
        else:
            self._animation_chars = ["□", "▢", "▣", "■"]  # `Unicode` 方块序列
            self._success_icon = "✓"  # `Unicode` 对勾
            self._failure_icon = "✗"  # `Unicode` 叉号
            self._farewell_emoji = "👋"  # `Unicode` 挥手

        self._animation_index = 0

    def add_step(self, title: str, description: str = "") -> None:
        """注册一个新步骤，供后续开始/完成。

        契约：`title` 必填、`description` 可空，追加到 `steps` 尾部；返回 `None`。
        副作用：修改 `steps` 列表与显示顺序。
        失败语义：不做字段校验，空标题将原样输出。
        关键路径：1) 组装步骤字典 2) 追加到列表。
        决策：保持步骤为字典而非小型数据类
        问题：避免引入额外依赖与序列化逻辑
        方案：使用最小字段集合的 `dict`
        代价：缺少类型约束，字段拼写错误需测试覆盖
        重评：当步骤字段稳定并需静态检查时改为 `dataclass`
        """
        self.steps.append(
            {
                "title": title,
                "description": description,
                "status": "pending",  # 状态：`pending` / `running` / `completed` / `failed`
                "start_time": None,
                "end_time": None,
            }
        )

    def _animate_step(self, step_index: int) -> None:
        """循环刷新当前步骤的动画字符。

        契约：仅在 `step_index` 有效且状态为 `running` 时输出；无返回。
        副作用：持续写入 `stdout`，依赖 `time.sleep(0.15)` 控制频率。
        失败语义：索引越界直接返回；`stdout` 写失败会抛异常由调用方处理。
        关键路径：1) 校验索引与状态 2) 覆盖当前行 3) 更新动画索引并 `sleep`。
        异常流：`sys.stdout.write` 失败会终止线程并传播异常。
        性能瓶颈：0.15s 刷新频率占用少量 `CPU`。
        排障入口：动画不动时检查 `_stop_animation` 与 `running` 标志。
        决策：用 `\r` 覆盖行而非打印新行
        问题：避免启动过程刷屏影响阅读
        方案：回车符重写当前行
        代价：在非交互终端可能看不到动画
        重评：若日志重定向成为主场景则改为每步单行输出
        """
        if step_index >= len(self.steps):
            return

        step = self.steps[step_index]

        while self.running and step["status"] == "running" and not self._stop_animation:
            # 实现：用回车覆盖当前行，避免输出滚屏
            sys.stdout.write("\r")

            animation_char = self._animation_chars[self._animation_index]

            line = f"{animation_char} {step['title']}..."
            sys.stdout.write(line)
            sys.stdout.flush()

            self._animation_index = (self._animation_index + 1) % len(self._animation_chars)

            time.sleep(0.15)

    def start_step(self, step_index: int) -> None:
        """开始指定步骤并启动动画线程。

        契约：`step_index` 为已有索引；返回 `None`。
        副作用：设置步骤状态与时间戳，启动 `daemon` 线程写 `stdout`。
        失败语义：索引越界直接返回；线程启动失败会向上抛异常。
        关键路径：1) 标记运行状态 2) 启动动画线程 3) 进入循环刷新。
        决策：动画线程设为 `daemon`
        问题：`CLI` 退出时不应被动画线程阻塞
        方案：使用 `daemon=True` 并在完成时显式 `join`
        代价：异常退出时可能有未刷新的终端行
        重评：若需要严格收尾或资源回收时改为非 `daemon`
        """
        if step_index >= len(self.steps):
            return

        self.current_step = step_index
        step = self.steps[step_index]
        step["status"] = "running"
        step["start_time"] = time.time()

        self.running = True
        self._stop_animation = False

        self._animation_thread = threading.Thread(target=self._animate_step, args=(step_index,))
        self._animation_thread.daemon = True
        self._animation_thread.start()

    def complete_step(self, step_index: int, *, success: bool = True) -> None:
        """完成步骤并输出成功/失败结果。

        契约：`step_index` 必须存在；`success` 决定状态与图标；无返回。
        副作用：停止动画线程、写入 `stdout`，更新耗时。
        失败语义：索引越界直接返回；线程 `join` 超时会继续执行输出。
        关键路径：1) 更新状态与时间 2) 停止并等待动画线程 3) 输出结果与耗时。
        异常流：`click.echo` 失败会抛异常给调用方。
        性能瓶颈：`join` 最多等待 0.5s。
        排障入口：终端行卡住时检查 `_animation_thread.is_alive()` 与 `_stop_animation`。
        决策：完成时立即输出单行结果
        问题：需要明确告知用户成功/失败与耗时
        方案：使用 `click` 的彩色输出并覆盖当前行
        代价：在不支持颜色的终端会退化为纯文本
        重评：若 `CLI` 改为结构化日志则输出格式需调整
        """
        if step_index >= len(self.steps):
            return

        step = self.steps[step_index]
        step["status"] = "completed" if success else "failed"
        step["end_time"] = time.time()

        # 实现：完成时先停动画线程，避免输出被动画覆盖
        self._stop_animation = True
        if self._animation_thread and self._animation_thread.is_alive():
            self._animation_thread.join(timeout=0.5)

        self.running = False

        # 实现：覆盖当前行后输出最终结果
        sys.stdout.write("\r")

        if success:
            icon = click.style(self._success_icon, fg="green", bold=True)
            title = click.style(step["title"], fg="green")
        else:
            icon = click.style(self._failure_icon, fg="red", bold=True)
            title = click.style(step["title"], fg="red")

        duration = ""
        if step["start_time"] and step["end_time"]:
            elapsed = step["end_time"] - step["start_time"]
            if self.verbose and elapsed > MIN_DURATION_THRESHOLD:
                duration = click.style(f" ({elapsed:.2f}s)", fg="bright_black")

        line = f"{icon} {title}{duration}"
        click.echo(line)

    def fail_step(self, step_index: int, error_msg: str = "") -> None:
        """标记步骤失败并按需输出错误信息。

        契约：`error_msg` 仅在 `verbose=True` 时显示；无返回。
        副作用：调用 `complete_step` 并写入 `stdout`。
        失败语义：与 `complete_step` 一致；错误信息为空则不输出。
        关键路径：1) 标记失败 2) 条件输出错误消息。
        决策：仅在 `verbose` 模式显示错误细节
        问题：避免默认输出干扰主流程信息
        方案：基于 `self.verbose` 控制
        代价：默认模式下排障信息不足
        重评：若用户反馈排障困难时考虑默认输出摘要
        """
        self.complete_step(step_index, success=False)
        if error_msg and self.verbose:
            click.echo(click.style(f"   Error: {error_msg}", fg="red"))

    @contextmanager
    def step(self, step_index: int) -> Generator[None, None, None]:
        """以上下文管理器方式运行步骤。

        契约：`with progress.step(i):` 块内异常会重新抛出；无返回值。
        副作用：自动调用 `start_step/complete_step` 并可能输出失败信息。
        失败语义：块内异常触发 `fail_step` 后继续向外抛；不会吞异常。
        关键路径：1) 启动步骤 2) 执行上下文 3) 成功完成或失败标记。
        决策：异常后仍抛出以保持调用栈
        问题：启动失败需要被上层捕获处理
        方案：记录失败后重新抛出
        代价：调用方必须显式处理异常
        重评：若未来需要软失败流程可改为返回状态
        """
        try:
            self.start_step(step_index)
            yield
            self.complete_step(step_index, success=True)
        except Exception as e:
            error_msg = str(e) if self.verbose else ""
            self.fail_step(step_index, error_msg)
            raise

    def print_summary(self) -> None:
        """输出初始化步骤汇总耗时。

        契约：仅在 `verbose=True` 且存在完成/失败步骤时输出；无返回。
        副作用：写入 `stdout`。
        失败语义：无步骤则静默返回。
        关键路径：1) 过滤已完成步骤 2) 汇总耗时 3) 输出总耗时。
        决策：仅显示总耗时而不列出每步明细
        问题：避免在 `verbose` 模式下输出过长
        方案：汇总为单行总耗时
        代价：无法直接定位慢步骤
        重评：当需要性能诊断时增加逐步明细选项
        """
        if not self.verbose:
            return

        completed_steps = [s for s in self.steps if s["status"] in ["completed", "failed"]]
        if not completed_steps:
            return

        total_time = sum(
            (s["end_time"] - s["start_time"]) for s in completed_steps if s["start_time"] and s["end_time"]
        )

        click.echo()
        click.echo(click.style(f"Total initialization time: {total_time:.2f}s", fg="bright_black"))

    def print_shutdown_summary(self) -> None:
        """输出关闭步骤汇总耗时。

        契约：仅在 `verbose=True` 且存在完成/失败步骤时输出；无返回。
        副作用：写入 `stdout`。
        失败语义：无步骤则静默返回。
        关键路径：1) 过滤关闭相关步骤 2) 汇总耗时 3) 输出总耗时。
        决策：关闭汇总保持与初始化一致的输出格式
        问题：统一用户心智，避免两套展示规则
        方案：沿用 `print_summary` 的统计逻辑
        代价：无法区分初始化/关闭的详细差异
        重评：当需要分别统计阶段耗时再拆分输出
        """
        if not self.verbose:
            return

        completed_steps = [s for s in self.steps if s["status"] in ["completed", "failed"]]
        if not completed_steps:
            return

        total_time = sum(
            (s["end_time"] - s["start_time"]) for s in completed_steps if s["start_time"] and s["end_time"]
        )

        click.echo()
        click.echo(click.style(f"Total shutdown time: {total_time:.2f}s", fg="bright_black"))


def create_langflow_progress(*, verbose: bool = False) -> ProgressIndicator:
    """构建带初始化步骤的 `ProgressIndicator`。

    契约：返回已注册启动步骤的实例；`verbose` 控制耗时输出。
    副作用：仅创建对象与内部步骤列表，不产生外部输入输出。
    失败语义：无显式异常；依赖 `ProgressIndicator` 初始化成功。
    关键路径：1) 创建指示器 2) 定义步骤顺序 3) 逐步注册。
    决策：步骤顺序与 `main.py` 启动链保持一致
    问题：用户期望看到与真实执行一致的提示
    方案：按启动流程固定顺序注册
    代价：新增启动步骤时需同步更新此列表
    重评：当启动流程可配置时改为从配置生成
    """
    progress = ProgressIndicator(verbose=verbose)

    # 注意：顺序需与启动链一致，否则用户对当前阶段的判断会偏差
    steps = [
        ("Initializing Langflow", "Setting up basic configuration"),
        ("Checking Environment", "Loading environment variables and settings"),
        ("Starting Core Services", "Initializing database and core services"),
        ("Connecting Database", "Setting up database connection and migrations"),
        ("Loading Components", "Caching component types and custom components"),
        ("Adding Starter Projects", "Creating or updating starter project templates"),
        ("Launching Langflow", "Starting server and final setup"),
    ]

    for title, description in steps:
        progress.add_step(title, description)

    return progress


def create_langflow_shutdown_progress(*, verbose: bool = False, multiple_workers: bool = False) -> ProgressIndicator:
    """构建带关闭步骤的 `ProgressIndicator`。

    契约：返回已注册关闭步骤的实例；`multiple_workers=True` 时标题包含 `PID`。
    副作用：多工作进程模式下会读取 `os.getpid()`。
    失败语义：`os.getpid` 失败会抛异常（极少见）。
    关键路径：1) 创建指示器 2) 按模式选择步骤 3) 注册步骤。
    决策：关闭顺序与初始化相反
    问题：需要先停服务再清理资源
    方案：按反向顺序展示，减少误解
    代价：若实际流程变化需同步更新
    重评：当关闭流程改为并行执行时调整提示顺序
    """
    progress = ProgressIndicator(verbose=verbose)

    # 注意：关闭步骤依赖初始化顺序，保持反向展示避免误解
    if multiple_workers:
        import os

        steps = [
            (f"[Worker PID {os.getpid()}] Stopping Server", "Gracefully stopping the web server"),
            (
                f"[Worker PID {os.getpid()}] Cancelling Background Tasks",
                "Stopping file synchronization and background jobs",
            ),
            (f"[Worker PID {os.getpid()}] Cleaning Up Services", "Teardown database connections and services"),
            (f"[Worker PID {os.getpid()}] Clearing Temporary Files", "Removing temporary directories and cache"),
            (f"[Worker PID {os.getpid()}] Finalizing Shutdown", "Completing cleanup and logging"),
        ]
    else:
        steps = [
            ("Stopping Server", "Gracefully stopping the web server"),
            ("Cancelling Background Tasks", "Stopping file synchronization and background jobs"),
            ("Cleaning Up Services", "Teardown database connections and services"),
            ("Clearing Temporary Files", "Removing temporary directories and cache"),
            ("Finalizing Shutdown", "Completing cleanup and logging"),
        ]

    for title, description in steps:
        progress.add_step(title, description)

    return progress
