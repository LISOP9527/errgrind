import json

from rich.live import Live
from rich.panel import Panel
from rich.text import Text

from ..application import (
    DrillStage,
    GrillState,
    InvalidWorkflowState,
    NoDrillContext,
    OutputContractError,
    WorkflowPersistenceError,
    WorkflowModelError,
)
from .state import AppState
from .ui import (
    console,
    multiline_input,
    select_from_list,
    select_error_split_view,
    sysmsg,
    errmsg,
    successmsg,
    popup_input,
    popup_content,
    popup_drill_answer,
    popup_confirm,
    render_markdown_to_formatted_text,
    render_markdown_to_plain_text,
    render_terminal_markdown,
)


COMMANDS: dict = {}
COMMAND_DESCRIPTIONS: dict[str, str] = {}


def _register(name, description, aliases=None):
    aliases = aliases or []
    COMMAND_DESCRIPTIONS[name] = description
    def wrap(fn):
        COMMANDS[name] = fn
        for a in aliases:
            COMMANDS[a] = fn
            COMMAND_DESCRIPTIONS.setdefault(a, description)
        return fn
    return wrap


def _show_context_recap(conversation_json: str, last_n: int = 3):
    messages = json.loads(conversation_json)
    user_assistant = [m for m in messages if m["role"] in ("user", "assistant")]
    recent = user_assistant[-last_n * 2:] if len(user_assistant) > last_n * 2 else user_assistant
    if recent:
        console.print("[dim] ──────────────────── 上次对话回顾 ────────────────────[/dim]")
        for msg in recent:
            role = "👤 你" if msg["role"] == "user" else "🧠 导师"
            text = render_markdown_to_plain_text(msg["content"])
            text = text[:150] + "..." if len(text) > 150 else text
            console.print(Text(f" {role}: {text}", style="dim"))
        console.print("[dim] ──────────────────────────────────────────────────────[/dim]")


def _show_grilling_record(error):
    messages = json.loads(error.grilling_conversation or "[]")
    body = []
    for message in messages:
        if message["role"] == "system" or (
            message["role"] == "user" and message["content"] == "开始吧"
        ):
            continue
        label = "你" if message["role"] == "user" else "导师"
        style = "class:user-input" if message["role"] == "user" else "class:label"
        body.append((style, f"{label}\n"))
        body.extend(render_markdown_to_formatted_text(message["content"]))
        body.append(("", "\n\n"))

    if not body:
        body = [("class:dim", "暂无 Grill 诊断对话记录")]
    popup_content(body, title=f"Error #{error.id} - Grill 诊断记录")


def _run_grill_call(state: AppState, operation):
    """Keep Rich streaming at the adapter boundary while Core owns the call."""
    if not callable(getattr(state.llm, "stream_chat", None)):
        with console.status("[bold cyan]🧠 导师思考中...", spinner="dots"):
            return operation()
    collected = []
    with Live(Text("🧠 导师思考中..."), refresh_per_second=15, transient=True) as live:
        def on_token(token: str):
            collected.append(token)
            live.update(render_terminal_markdown("".join(collected)))

        return operation(on_token=on_token)


def _render_grill_response(result):
    if result.assistant_response is not None:
        console.print(Panel(
            render_terminal_markdown(result.assistant_response),
            title="[bold cyan]🧠 导师 (Grill)[/bold cyan]",
            border_style="cyan", padding=(1, 2),
        ))


def _pause_grill_safely(state: AppState, error_id: int) -> None:
    """The submitted user message was already saved before model work."""
    try:
        state.application().pause_grill(error_id)
    except Exception:
        # A second persistence failure must not turn a recoverable Grill error
        # into a crashed CLI session.  The pre-call save remains authoritative.
        pass


def _run_grilling(state: AppState, error):
    initial_result = None
    if error.status != "pending-grill":
        initial_result = state.application().start_or_resume_grill(error.id)
        if initial_result.state == GrillState.READ_ONLY:
            _show_grilling_record(initial_result.error)
            return

    if error.grilling_conversation and error.id not in state.accessed_error_ids:
        state.accessed_error_ids.add(error.id)
        _show_context_recap(error.grilling_conversation)
    try:
        result = initial_result or _run_grill_call(
            state,
            lambda **kwargs: state.application().start_or_resume_grill(
                error.id, **kwargs
            ),
        )
        if result.state == GrillState.READ_ONLY:
            _show_grilling_record(result.error)
            return
        _render_grill_response(result)
        if result.state == GrillState.COMPLETE:
            successmsg("Grill 诊断完成！状态已更新为待讲解")
            if popup_confirm("Grill 诊断已完成，是否立即开始讲解？"):
                _run_teaching(state, result.error)
            return

        max_turns = state.cfg.get("grill_max_turns", 30)
        for _ in range(max_turns):
            reply = multiline_input("[bold cyan]👤 你的回答[/bold cyan]")
            result = _run_grill_call(
                state,
                lambda **kwargs: state.application().submit_grill_answer(error.id, reply, **kwargs),
            )
            _render_grill_response(result)
            if result.state == GrillState.COMPLETE:
                successmsg("Grill 诊断完成！状态已更新为待讲解")
                if popup_confirm("Grill 诊断已完成，是否立即开始讲解？"):
                    _run_teaching(state, result.error)
                return
        state.application().pause_grill(error.id)
        sysmsg("已达到最大对话轮数，对话已保存")
    except InvalidWorkflowState as exc:
        errmsg(str(exc))
    except (WorkflowModelError, OutputContractError, WorkflowPersistenceError) as exc:
        errmsg(str(exc))
        _pause_grill_safely(state, error.id)
    except (KeyboardInterrupt, EOFError):
        sysmsg("Grill 诊断对话已保存（中断）")
        _pause_grill_safely(state, error.id)


def _run_teaching(state: AppState, error):
    if error.teach_conversation:
        _show_context_recap(error.teach_conversation)

    def show_thinking() -> None:
        console.print("[dim]🧠 导师思考中...[/dim]")

    try:
        result = state.application().start_or_resume_teach(
            error.id, before_model_call=show_thinking
        )
        if result.assistant_response is not None:
            title = "[bold green]📖 针对性讲解 (Teach)[/bold green]"
            if error.teach_conversation:
                title = "[bold green]📖 针对性解答[/bold green]"
            console.print(Panel(render_terminal_markdown(result.assistant_response), title=title, border_style="green", padding=(1, 2)))

        while True:
            reply = multiline_input("[bold green]💬 提问/讨论（Ctrl+C 保存并退出）[/bold green]")
            result = state.application().submit_teach_answer(
                error.id, reply, before_model_call=show_thinking
            )
            console.print(Panel(render_terminal_markdown(result.assistant_response), title="[bold green]📖 针对性解答[/bold green]", border_style="green", padding=(1, 2)))
    except InvalidWorkflowState as exc:
        sysmsg(str(exc))
    except WorkflowModelError as exc:
        errmsg(str(exc))
    except (KeyboardInterrupt, EOFError):
        state.application().finish_teach(error.id)
        successmsg("讲解对话已保存，可随时继续")


@_register("record", "记录一个 error")
def _cmd_record(state, arg):
    question = popup_input("📝 记录 Error (1/3)", "请粘贴你做错的题目：")
    if not question or not question.strip():
        sysmsg("记录已取消")
        return

    while True:
        user_thoughts = popup_input(
            "📝 记录 Error (2/3)",
            "请写出你当时的思路，哪怕只有一句话；确实没有思路可填写「没有思路」：",
        )
        if user_thoughts is None:
            sysmsg("记录已取消")
            return
        if user_thoughts.strip():
            break
        errmsg("用户思路不能为空，请描述你当时是怎么想的")

    reference_answer = popup_input(
        "📝 记录 Error (3/3)",
        "若有参考答案请粘贴，若暂无请按 Enter 跳过：",
    )
    if reference_answer is None:
        sysmsg("记录已取消")
        return

    error_id = state.db.create_error(
        question=question.strip(),
        user_thoughts=user_thoughts.strip() or None,
        reference_answer=reference_answer.strip() or None,
        origin="record",
    )
    successmsg(f"已成功录入错题库 (Error #{error_id})")
    sysmsg("输入 /resume 开始处理错题")


@_register("ocr", "从图片识别并记录一个 error")
def _cmd_ocr(state, arg):
    image_path = arg.strip() if arg and arg.strip() else popup_input(
        "📷 OCR 录题",
        "请输入数学错题图片路径（支持 PNG、JPEG、WebP，最大 20 MB；图片会发送给当前 AI provider）：",
        multiline=False,
    )
    if not image_path or not image_path.strip():
        sysmsg("OCR 录题已取消")
        return

    ocr_image = getattr(state.llm, "ocr_image", None)
    if not callable(ocr_image):
        errmsg("当前 AI provider 不支持图片 OCR，请在 /config 中切换 provider")
        return

    try:
        prompt = state.prompts.load("ocr.md")
        with console.status("[bold cyan]🔎 正在识别数学题图片...[/bold cyan]", spinner="dots"):
            extracted = ocr_image(image_path.strip(), prompt)
    except (KeyboardInterrupt, EOFError):
        sysmsg("OCR 录题已取消")
        return
    except Exception as exc:
        errmsg(f"OCR 失败: {exc}")
        return

    question = popup_input(
        "📷 OCR 校对 (1/3)",
        "请校对题目；可以直接修改识别错误：",
        initial_text=extracted.get("question", ""),
    )
    if not question or not question.strip():
        sysmsg("OCR 录题已取消，未写入错题库")
        return

    while True:
        user_thoughts = popup_input(
            "📷 OCR 校对 (2/3)",
            "请校对你的原始思路；图片中没有思路时填写「没有思路」：",
            initial_text=extracted.get("user_thoughts", ""),
        )
        if user_thoughts is None:
            sysmsg("OCR 录题已取消，未写入错题库")
            return
        if user_thoughts.strip():
            break
        errmsg("用户思路不能为空，请描述你当时是怎么想的")

    reference_answer = popup_input(
        "📷 OCR 校对 (3/3)",
        "请校对参考答案；图片中没有时可留空：",
        initial_text=extracted.get("reference_answer", ""),
    )
    if reference_answer is None:
        sysmsg("OCR 录题已取消，未写入错题库")
        return

    error_id = state.db.create_error(
        question=question.strip(),
        user_thoughts=user_thoughts.strip(),
        reference_answer=reference_answer.strip() or None,
        origin="ocr",
    )
    successmsg(f"OCR 校对完成，已录入错题库 (Error #{error_id})")
    sysmsg("输入 /resume 开始处理错题")


@_register("resume", "查看/处理 error（双栏工作台 → grill/teach）")
def _cmd_resume(state, arg):
    while True:
        errors = state.application().list_errors()
        if not errors:
            sysmsg("暂无 error 记录，用 /record 添加")
            return

        idx, action = select_error_split_view(errors)
        if idx is None or action is None:
            return

        error = state.application().get_error(errors[idx].id)
        if error is None:
            continue

        state.accessed_error_ids.add(error.id)

        if action == "g":
            _run_grilling(state, error)
        elif action == "t":
            _run_teaching(state, error)
        elif action == "d":
            confirmed = popup_confirm(f"确认删除 Error #{idx + 1}？")
            if confirmed:
                state.application().delete_error(error.id)
                successmsg(f"Error #{idx + 1} 已从错题库删除")


@_register("drill", "出综合练习题")
def _cmd_drill(state, arg):
    def show_stage(stage: DrillStage) -> None:
        if stage == DrillStage.SPEC:
            sysmsg("🧠 正在从近期 Error 中提炼出题规格...")
        elif stage == DrillStage.DRAFT:
            sysmsg("🧩 正在根据出题规格生成新题...")

    try:
        preparation = state.application().prepare_drill(on_stage=show_stage)
    except NoDrillContext as exc:
        sysmsg(str(exc))
        return
    except Exception as exc:
        errmsg(str(exc))
        return

    user_response = popup_drill_answer(preparation.question)
    if not user_response:
        sysmsg("已取消作答")
        return

    sysmsg("⚖️ 判分评估中...")
    try:
        judgment = state.application().judge_and_record_drill(preparation, user_response)
    except Exception as exc:
        errmsg(str(exc))
        return

    if judgment.is_correct:
        popup_content(
            "本次演练判定为正确。该结果会作为一次干预记录保存，"
            "不等于未来错误已减少。",
            title="演练评估结果",
        )
        successmsg("本次演练判定为正确，结果已记录")
    else:
        body = [("bold red", "❌ 本次演练判定为错误\n\n")]
        if judgment.feedback:
            body.append(("class:label", "💡 评估反馈:\n"))
            body.extend(render_markdown_to_formatted_text(judgment.feedback))
            body.append(("", "\n"))
        popup_content(body, title="演练评估结果")
        errmsg("答错了，已自动将此衍生题作为新 Error 入库")

        sysmsg(
            f"新 error (ID: #{judgment.attempt.derived_error_id}) 已入库，"
            "可随时输入 /resume 处理"
        )


@_register("status", "查看 error 状态统计")
def _cmd_status(state, arg):
    counts = state.db.count_by_status()
    origin_counts = state.db.count_by_origin()
    drill_stats = state.db.drill_stats()
    total = counts['total']

    def make_bar(cnt, total_num, width=15):
        if total_num == 0:
            return "[░░░░░░░░░░░░░░░]   0.0%"
        pct = (cnt / total_num) * 100
        filled = int(round((cnt / total_num) * width))
        bar = "█" * filled + "░" * (width - filled)
        return f"[{bar}]  {pct:>5.1f}%"

    body = [
        ("class:title", "📊 ErrGrind 错题分析看板\n\n"),
        ("", "  ⏳ 待诊断 (pending-grill):  "),
        ("class:badge-grill", f"{counts['pending-grill']:<3} "),
        ("", f"{make_bar(counts['pending-grill'], total)}\n"),

        ("", "  📖 待讲解 (pending-teach):  "),
        ("class:badge-teach", f"{counts['pending-teach']:<3} "),
        ("", f"{make_bar(counts['pending-teach'], total)}\n"),

        ("", "  ✅ 已完成 (done):           "),
        ("class:badge-done", f"{counts['done']:<3} "),
        ("", f"{make_bar(counts['done'], total)}\n"),

        ("class:dim", "  ───────────────────────────────────────────────────\n"),
        ("class:subtitle", f"  🗂  数据库累计错题总数:  {total} 条\n"),
        (
            "class:dim",
            "  来源: "
            + "；".join(
                f"{label}={origin_counts[key]}"
                for key, label in (
                    ("record", "手动录入"),
                    ("ocr", "OCR 校对"),
                    ("drill", "Drill 衍生"),
                    ("unknown", "历史来源未知"),
                )
            )
            + "\n",
        ),
        (
            "class:dim",
            f"  Drill 干预记录: 总计 {drill_stats['total']} 次，"
            f"正确 {drill_stats['correct']} 次，"
            f"错误 {drill_stats['incorrect']} 次\n",
        ),
        (
            "class:dim",
            "  注：Drill 结果不等于未来真实 Error 减少的证明。\n",
        ),
        (
            "class:dim",
            "  注：删除 Error 会改变累计统计；不同 Judge 版本不可直接比较。\n",
        ),
    ]
    popup_content(body, title="错题库状态看板")


@_register("config", "编辑配置（上下文条数、最大轮数、提供商）")
def _cmd_config(state, arg):
    from ..config import save as save_config
    from .app import _change_provider, _make_llm

    items = [
        ("drill_context_n", f"drill_context_n = {state.cfg.get('drill_context_n', 10)}"),
        ("grill_max_turns", f"grill_max_turns = {state.cfg.get('grill_max_turns', 30)}"),
        ("provider", f"provider = {state.cfg.get('provider', '?')}"),
    ]

    while True:
        idx = select_from_list(
            [item[1] for item in items],
            lambda x: x,
            title="系统参数配置",
            footer="[↑↓/PgUp/PgDn] 选择   [Enter] 编辑   [q/Esc] 返回",
        )
        if idx is None:
            return

        key = items[idx][0]

        if key == "drill_context_n":
            current = state.cfg.get("drill_context_n", 10)
            val = popup_input(
                "编辑 drill_context_n",
                f"输入新值（当前: {current}）：",
                multiline=False,
            )
            if val is None:
                sysmsg("取消")
                continue
            try:
                n = int(val.strip())
                if n < 1:
                    errmsg("必须 >= 1")
                    continue
                state.cfg["drill_context_n"] = n
                save_config(state.cfg)
                successmsg(f"drill_context_n 已设置为 {n}")
                items[0] = (key, f"drill_context_n = {n}")
            except ValueError:
                errmsg("请输入整数")

        elif key == "grill_max_turns":
            current = state.cfg.get("grill_max_turns", 30)
            val = popup_input(
                "编辑 grill_max_turns",
                f"输入新值（当前: {current}）：",
                multiline=False,
            )
            if val is None:
                sysmsg("取消")
                continue
            try:
                n = int(val.strip())
                if n < 1:
                    errmsg("必须 >= 1")
                    continue
                state.cfg["grill_max_turns"] = n
                save_config(state.cfg)
                successmsg(f"grill_max_turns 已设置为 {n}")
                items[1] = (key, f"grill_max_turns = {n}")
            except ValueError:
                errmsg("请输入整数")

        elif key == "provider":
            try:
                candidate = dict(state.cfg)
                _change_provider(candidate)
                new_llm = _make_llm(candidate)
                save_config(candidate)

                previous_llm = state.llm
                state.cfg.clear()
                state.cfg.update(candidate)
                state.llm = new_llm
                close_previous = getattr(previous_llm, "close", None)
                if callable(close_previous):
                    close_previous()
                successmsg(f"provider 已切换为 {state.cfg['provider']}")
                items[2] = (key, f"provider = {state.cfg['provider']}")
            except (KeyboardInterrupt, EOFError):
                sysmsg("取消")


@_register("model", "切换 AI 模型")
def _cmd_model(state, arg):
    from ..config import save as save_config
    from .app import _select_model, _make_llm

    try:
        candidate = dict(state.cfg)
        _select_model(candidate)
        new_llm = _make_llm(candidate)
        save_config(candidate)

        previous_llm = state.llm
        state.cfg.clear()
        state.cfg.update(candidate)
        state.llm = new_llm
        close_previous = getattr(previous_llm, "close", None)
        if callable(close_previous):
            close_previous()
        successmsg(f"model 已切换为 {state.cfg.get('model', '?')}")
    except (KeyboardInterrupt, EOFError):
        sysmsg("取消")


@_register("help", "显示此帮助")
def _cmd_help(state, arg):
    groups = [
        ("📝 错题与演练工作流", [
            ("/record", "录入一个新的 error (进入 pending-grill)"),
            ("/ocr [路径]", "识别图片，校对后录入 error"),
            ("/resume", "打开双栏工作台 (进行 Grill 诊断 / Teach 讲解)"),
            ("/drill", "结合近期 error 生成综合演练测试题"),
        ]),
        ("📊 状态与看板", [
            ("/status", "查看错题库状态与完成进度图表看板"),
        ]),
        ("⚙️ 系统与模型配置", [
            ("/config", "调整 drill 条数、grill 轮数或 AI 提供商"),
            ("/model", "快捷切换 AI 模型（Gemini / DeepSeek / OpenCode / Codex）"),
            ("/help", "显示本命令帮助指南"),
            ("/exit", "退出 ErrGrind 应用"),
        ])
    ]

    body = [("class:title", "✦ ErrGrind Slash 命令指南\n\n")]
    for group_title, cmds in groups:
        body.append(("class:subtitle", f" {group_title}\n"))
        for cmd_name, desc in cmds:
            body.append(("", f"   {cmd_name:<10}  {desc}\n"))
        body.append(("", "\n"))

    popup_content(body, title="命令指南")


@_register("exit", "退出")
def _cmd_exit(state, arg):
    raise SystemExit(0)
