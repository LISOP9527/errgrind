import json
import sys
from datetime import datetime

from rich.panel import Panel

from .state import AppState
from .ui import console, user_input, multiline_input, select_from_list, prompt_line, sysmsg, errmsg


EXIT_KEYWORDS = ["理解了", "明白了", "好了", "结束"]
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


def _status_color(status: str) -> str:
    return {
        "pending-grill": "yellow",
        "pending-teach": "cyan",
        "done": "green",
    }.get(status, "white")


def _format_error_row(error) -> str:
    q = error.question[:30].replace("\n", " ")
    if len(error.question) > 30:
        q += "..."
    created = error.created_at.strftime("%Y-%m-%d %H:%M")
    color = _status_color(error.status)
    return f"#{error.id:<3} [{color}]{error.status}[/{color}]  {q}  [dim]{created}[/dim]"


def _format_error_detail(error) -> str:
    lines = [
        f"  状态: [{_status_color(error.status)}]{error.status}[/{_status_color(error.status)}]",
        f"  创建: {error.created_at.strftime('%Y-%m-%d %H:%M')}",
        f"  更新: {error.updated_at.strftime('%Y-%m-%d %H:%M')}",
        "",
        f"  题目:",
        f"    {error.question}",
    ]
    if error.user_thoughts:
        lines.append("")
        lines.append(f"  用户思路概述:")
        lines.append(f"    {error.user_thoughts}")
    if error.reference_answer:
        lines.append("")
        lines.append(f"  参考答案及解析:")
        lines.append(f"    {error.reference_answer}")
    return "\n".join(lines)


def _show_detail_page(state: AppState, error):
    console.print(Panel(_format_error_detail(error), title=f"Error #{error.id}", border_style="blue"))

    if error.status == "pending-grill" and error.grilling_conversation:
        console.print("[yellow][!] 上次 grilling 中途中断，按 g 继续[/yellow]")

    if error.grilling_summary:
        console.print(Panel(error.grilling_summary, title="Grilling 摘要", border_style="cyan"))

    if error.status == "pending-grill":
        console.print("\n[dim]下一步：开始分析这个错误（grill）[/dim]")
    elif error.status == "pending-teach":
        console.print("\n[dim]下一步：讲解此题（teach）[/dim]")

    actions = [("g", "grill"), ("b", "back")]
    if error.grilling_conversation:
        actions.insert(1, ("t", "teach"))

    idx = select_from_list(
        [f"[{k}]{name}" for k, name in actions],
        lambda x: x,
        footer="[Enter]选择 [q]返回",
    )
    if idx is None:
        return "b"
    return actions[idx][0]


def _show_context_recap(conversation_json: str, last_n: int = 3):
    messages = json.loads(conversation_json)
    user_assistant = [m for m in messages if m["role"] in ("user", "assistant")]
    recent = user_assistant[-last_n * 2:] if len(user_assistant) > last_n * 2 else user_assistant
    if recent:
        console.print("[dim]--- 上次对话回顾 ---[/dim]")
        for msg in recent:
            role = "你" if msg["role"] == "user" else "导师"
            text = msg["content"][:150] + "..." if len(msg["content"]) > 150 else msg["content"]
            console.print(f"[dim]{role}: {text}[/dim]")
        console.print("[dim]---[/dim]")


def _run_grilling(state: AppState, error):
    system_prompt = state.prompts.load("grilling.md").format(
        question=error.question,
        user_thoughts=error.user_thoughts or "",
        reference_answer=error.reference_answer or "",
    )

    has_conversation = bool(error.grilling_conversation)
    messages = json.loads(error.grilling_conversation) if has_conversation else [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": "开始吧"},
    ]

    if has_conversation and error.status == "done":
        state.db.clear_teach_and_summary(error.id)
        state.db.set_status(error.id, "pending-teach")
        if error.id not in state.accessed_error_ids:
            state.accessed_error_ids.add(error.id)
            _show_context_recap(error.grilling_conversation)
    elif has_conversation and error.id not in state.accessed_error_ids:
        state.accessed_error_ids.add(error.id)
        _show_context_recap(error.grilling_conversation)

    if not has_conversation:
        console.print("[dim]思考中...[/dim]")
        try:
            resp = state.llm.chat(messages)
        except Exception as e:
            console.print(f"[bold red]API 错误: {e}[/bold red]")
            return
        console.print(Panel(resp, title="导师", border_style="cyan"))
        messages.append({"role": "assistant", "content": resp})

        if resp.strip().endswith("[GRILLING_END]"):
            summary = resp[: -len("[GRILLING_END]")].strip()
            messages[-1]["content"] = summary
            state.db.update_grilling(error.id, json.dumps(messages, ensure_ascii=False), summary)
            sysmsg("✓ Grilling 完成")
            return
    else:
        if messages and messages[-1]["role"] == "user":
            console.print("[dim]正在补全上次的回应...[/dim]")
            try:
                resp = state.llm.chat(messages)
            except Exception as e:
                console.print(f"[bold red]API 错误: {e}[/bold red]")
                return
            console.print(Panel(resp, title="导师", border_style="cyan"))
            messages.append({"role": "assistant", "content": resp})

            if resp.strip().endswith("[GRILLING_END]"):
                summary = resp[: -len("[GRILLING_END]")].strip()
                messages[-1]["content"] = summary
                state.db.update_grilling(error.id, json.dumps(messages, ensure_ascii=False), summary)
                sysmsg("✓ Grilling 完成")
                return

    max_turns = state.cfg.get("grill_max_turns", 30)
    try:
        for _ in range(max_turns):
            reply = multiline_input("[bold cyan]你的回答[/bold cyan]")
            messages.append({"role": "user", "content": reply})

            console.print("[dim]思考中...[/dim]")
            try:
                resp = state.llm.chat(messages)
            except Exception as e:
                console.print(f"[bold red]API 错误: {e}[/bold red]")
                state.db.save_grilling_conversation(error.id, json.dumps(messages, ensure_ascii=False))
                state.db.set_status(error.id, "pending-grill")
                return

            console.print(Panel(resp, title="导师", border_style="cyan"))
            messages.append({"role": "assistant", "content": resp})

            if resp.strip().endswith("[GRILLING_END]"):
                summary = resp[: -len("[GRILLING_END]")].strip()
                messages[-1]["content"] = summary
                state.db.update_grilling(error.id, json.dumps(messages, ensure_ascii=False), summary)
                sysmsg("✓ Grilling 完成")
                return
    except (KeyboardInterrupt, EOFError):
        sysmsg("Grilling 中断")
        state.db.save_grilling_conversation(error.id, json.dumps(messages, ensure_ascii=False))
        state.db.set_status(error.id, "pending-grill")


def _run_teaching(state: AppState, error):
    if not error.grilling_conversation:
        sysmsg("请先 grill")
        return

    is_partial = False
    messages = []
    if error.teach_conversation:
        existing = json.loads(error.teach_conversation)
        if existing and existing[-1]["role"] == "user":
            is_partial = True
            messages = existing

    if not is_partial:
        history = "\n".join(
            f"{'导师' if m['role'] == 'assistant' else '学生'}：{m['content']}"
            for m in json.loads(error.grilling_conversation) if m["role"] != "system"
        )

        system_prompt = state.prompts.load("teach.md").format(
            question=error.question,
            user_thoughts=error.user_thoughts or "",
            reference_answer=error.reference_answer or "",
            grilling_history=history,
        )

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": "请开始讲解"},
        ]

        console.print("[dim]思考中...[/dim]")
        try:
            resp = state.llm.chat(messages)
        except Exception as e:
            console.print(f"[bold red]API 错误: {e}[/bold red]")
            return
        console.print(Panel(resp, title="讲解", border_style="green"))
        messages.append({"role": "assistant", "content": resp})
    else:
        console.print("[dim]正在补全上次的回应...[/dim]")
        try:
            resp = state.llm.chat(messages)
        except Exception as e:
            console.print(f"[bold red]API 错误: {e}[/bold red]")
            return
        console.print(Panel(resp, title="解答", border_style="green"))
        messages.append({"role": "assistant", "content": resp})

    try:
        while True:
            reply = multiline_input("[bold green]有疑问可以继续提问，输入「理解了」结束[/bold green]")
            if any(kw in reply for kw in EXIT_KEYWORDS):
                state.db.update_teach(error.id, json.dumps(messages, ensure_ascii=False))
                sysmsg("✓ 讲解完成")
                return
            messages.append({"role": "user", "content": reply})
            console.print("[dim]思考中...[/dim]")
            try:
                resp = state.llm.chat(messages)
            except Exception as e:
                console.print(f"[bold red]API 错误: {e}[/bold red]")
                state.db.save_teach_conversation(error.id, json.dumps(messages, ensure_ascii=False))
                return
            console.print(Panel(resp, title="解答", border_style="green"))
            messages.append({"role": "assistant", "content": resp})
    except (KeyboardInterrupt, EOFError):
        sysmsg("讲解中断")
        state.db.save_teach_conversation(error.id, json.dumps(messages, ensure_ascii=False))


@_register("record", "记录一个 error")
def _cmd_record(state, arg):
    question = multiline_input("请粘贴你做错的题目")
    if not question:
        console.print("[red]题目不能为空[/red]")
        return
    user_thoughts = multiline_input("你的思路概述（可留空，留空表示思考过但没做出答案）")
    reference_answer = multiline_input("参考答案及解析（不知道可留空）")

    error_id = state.db.create_error(
        question=question,
        user_thoughts=user_thoughts or None,
        reference_answer=reference_answer or None,
    )
    sysmsg(f"✓ Error #{error_id} 已记录")
    sysmsg("输入 /resume 开始处理")


@_register("resume", "查看/处理 error（列表 → grill/teach）")
def _cmd_resume(state, arg):
    errors = state.db.list_all_errors()
    if not errors:
        sysmsg("暂无 error 记录，用 /record 添加")
        return

    while True:
        idx = select_from_list(
            errors,
            lambda e: _format_error_row(e),
            title="Error 列表",
        )
        if idx is None:
            return

        error = state.db.get_error(errors[idx].id)
        if error is None:
            continue

        while True:
            error = state.db.get_error(error.id)
            action = _show_detail_page(state, error)
            if action == "g":
                _run_grilling(state, error)
            elif action == "t":
                _run_teaching(state, error)
            elif action == "b":
                break


@_register("drill", "出综合练习题")
def _cmd_drill(state, arg):
    n = state.cfg.get("drill_context_n", 10)
    context = state.db.get_drill_context(n)

    if not context:
        sysmsg("暂无可用于出题的 error，请先 /resume 完成 grilling")
        return

    summary_list = "\n\n".join(
        f"[题目 {i+1}]\n{q}\n[审讯摘要]\n{s}"
        for i, (q, s) in enumerate(context)
    )

    drill_prompt = state.prompts.load("drill.md").format(summary_list=summary_list)

    console.print("[dim]思考中...[/dim]")
    try:
        result = state.llm.chat_json([{"role": "user", "content": drill_prompt}])
    except Exception as e:
        errmsg(f"生成题目失败: {e}")
        return

    question = result["question"]
    reference_answer = result["reference_answer"]

    console.print(Panel(f"[bold]{question}[/bold]", title="练习题", border_style="yellow"))

    user_response = multiline_input("[bold yellow]请输入你的答案和思路（思路可选）[/bold yellow]")
    if not user_response:
        sysmsg("未作答，取消")
        return

    judge_prompt = state.prompts.load("judge.md").format(
        question=question,
        reference_answer=reference_answer,
        user_response=user_response,
    )

    console.print("[dim]判分中...[/dim]")
    try:
        judgment = state.llm.chat_json([{"role": "user", "content": judge_prompt}])
    except Exception as e:
        errmsg(f"判分失败: {e}")
        return

    is_correct = bool(judgment.get("is_correct", False))
    feedback = judgment.get("feedback", "")

    if is_correct:
        sysmsg("✓ 回答正确")
    else:
        sysmsg("✗ 答错了")
        if feedback:
            console.print(Panel(feedback, title="反馈", border_style="red"))

        new_id = state.db.create_error(
            question=question,
            user_thoughts=user_response,
            reference_answer=reference_answer,
        )
        sysmsg(f"新 error #{new_id} 已入库，用 /resume 处理")


@_register("status", "查看 error 状态统计")
def _cmd_status(state, arg):
    counts = state.db.count_by_status()
    lines = [
        f"  [yellow]待 grill:[/yellow]  {counts['pending-grill']}",
        f"  [cyan]待 teach:[/cyan]  {counts['pending-teach']}",
        f"  [green]已完成:[/green]    {counts['done']}",
        f"  总计:      {counts['total']}",
    ]
    console.print(Panel("\n".join(lines), title="状态", border_style="blue"))


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
            title="配置项",
        )
        if idx is None:
            return

        key = items[idx][0]

        if key == "drill_context_n":
            val = user_input(
                f"新的 drill_contextn (当前: {state.cfg.get('drill_context_n', 10)})",
                default=str(state.cfg.get('drill_context_n', 10)),
            )
            try:
                n = int(val)
                if n < 1:
                    errmsg("必须 >= 1")
                    continue
                state.cfg["drill_context_n"] = n
                save_config(state.cfg)
                sysmsg(f"✓ drill_context_n = {n}")
                items[0] = (key, f"drill_context_n = {n}")
            except ValueError:
                errmsg("请输入整数")

        elif key == "grill_max_turns":
            val = user_input(
                f"新的 grill_max_turns (当前: {state.cfg.get('grill_max_turns', 30)})",
                default=str(state.cfg.get('grill_max_turns', 30)),
            )
            try:
                n = int(val)
                if n < 1:
                    errmsg("必须 >= 1")
                    continue
                state.cfg["grill_max_turns"] = n
                save_config(state.cfg)
                sysmsg(f"✓ grill_max_turns = {n}")
                items[1] = (key, f"grill_max_turns = {n}")
            except ValueError:
                errmsg("请输入整数")

        elif key == "provider":
            try:
                _change_provider(state.cfg)
                state.llm = _make_llm(state.cfg)
                save_config(state.cfg)
                sysmsg(f"✓ provider = {state.cfg['provider']}")
                items[2] = (key, f"provider = {state.cfg['provider']}")
            except (KeyboardInterrupt, EOFError):
                sysmsg("取消")


@_register("model", "切换 AI 模型")
def _cmd_model(state, arg):
    from ..config import save as save_config
    from .app import _select_model, _make_llm

    try:
        _select_model(state.cfg)
        state.llm = _make_llm(state.cfg)
        save_config(state.cfg)
        sysmsg(f"✓ model = {state.cfg.get('model', '?')}")
    except (KeyboardInterrupt, EOFError):
        sysmsg("取消")


@_register("help", "显示此帮助", ["h"])
def _cmd_help(state, arg):
    seen = set()
    rows = []
    width = 0
    for name in COMMANDS:
        if name in seen:
            continue
        seen.add(name)
        desc = COMMAND_DESCRIPTIONS.get(name, "")
        rows.append((name, desc))
        width = max(width, len(name))
    lines = ["[bold]命令列表[/bold]", ""]
    for name, desc in rows:
        lines.append(f"  [bold]/{name:<{width}}[/bold]  {desc}")
    console.print(Panel("\n".join(lines), title="帮助", border_style="blue"))


@_register("exit", "退出", ["quit"])
def _cmd_exit(state, arg):
    raise SystemExit(0)
