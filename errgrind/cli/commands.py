import json

from rich.live import Live
from rich.markdown import Markdown
from rich.panel import Panel
from rich.text import Text

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
)


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


def _show_context_recap(conversation_json: str, last_n: int = 3):
    messages = json.loads(conversation_json)
    user_assistant = [m for m in messages if m["role"] in ("user", "assistant")]
    recent = user_assistant[-last_n * 2:] if len(user_assistant) > last_n * 2 else user_assistant
    if recent:
        console.print("[dim] ──────────────────── 上次对话回顾 ────────────────────[/dim]")
        for msg in recent:
            role = "👤 你" if msg["role"] == "user" else "🧠 导师"
            text = msg["content"][:150] + "..." if len(msg["content"]) > 150 else msg["content"]
            console.print(f" [dim]{role}: {text}[/dim]")
        console.print("[dim] ──────────────────────────────────────────────────────[/dim]")


def _llm_chat(messages, llm):
    stream_chat = getattr(llm, "stream_chat", None)
    if not callable(stream_chat):
        with console.status("[bold cyan]🧠 导师思考中...", spinner="dots"):
            return llm.chat(messages)

    collected = []
    with Live(Text("🧠 导师思考中..."), refresh_per_second=15) as live:
        def on_token(token: str):
            collected.append(token)
            live.update(Text("".join(collected)))

        return stream_chat(messages, on_token)


def _is_grilling_complete(response: str) -> bool:
    return response.strip().endswith("[GRILLING_END]")


def _grilling_summary(response: str) -> str:
    return response.strip()[: -len("[GRILLING_END]")].strip()


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
        try:
            resp = _llm_chat(messages, state.llm)
        except Exception as e:
            errmsg(f"API 错误: {e}")
            return
        complete = _is_grilling_complete(resp)
        clean_text = _grilling_summary(resp) if complete else resp
        console.print(Panel(Markdown(clean_text), title="[bold cyan]🧠 导师 (Grill)[/bold cyan]", border_style="cyan", padding=(1, 2)))
        messages.append({"role": "assistant", "content": resp})

        if complete:
            summary = _grilling_summary(resp)
            messages[-1]["content"] = summary
            state.db.update_grilling(error.id, json.dumps(messages, ensure_ascii=False), summary)
            successmsg("Grilling 思维审讯完成")
            return
    else:
        if messages and messages[-1]["role"] == "user":
            try:
                resp = _llm_chat(messages, state.llm)
            except Exception as e:
                errmsg(f"API 错误: {e}")
                return
            complete = _is_grilling_complete(resp)
            clean_text = _grilling_summary(resp) if complete else resp
            console.print(Panel(Markdown(clean_text), title="[bold cyan]🧠 导师 (Grill)[/bold cyan]", border_style="cyan", padding=(1, 2)))
            messages.append({"role": "assistant", "content": resp})

            if complete:
                summary = _grilling_summary(resp)
                messages[-1]["content"] = summary
                state.db.update_grilling(error.id, json.dumps(messages, ensure_ascii=False), summary)
                successmsg("Grilling 思维审讯完成")
                return

    max_turns = state.cfg.get("grill_max_turns", 30)
    try:
        for _ in range(max_turns):
            reply = multiline_input("[bold cyan]👤 你的回答[/bold cyan]")
            messages.append({"role": "user", "content": reply})

            try:
                resp = _llm_chat(messages, state.llm)
            except Exception as e:
                errmsg(f"API 错误: {e}")
                state.db.save_grilling_conversation(error.id, json.dumps(messages, ensure_ascii=False))
                state.db.set_status(error.id, "pending-grill")
                return

            complete = _is_grilling_complete(resp)
            clean_text = _grilling_summary(resp) if complete else resp
            console.print(Panel(Markdown(clean_text), title="[bold cyan]🧠 导师 (Grill)[/bold cyan]", border_style="cyan", padding=(1, 2)))
            messages.append({"role": "assistant", "content": resp})

            if complete:
                summary = _grilling_summary(resp)
                messages[-1]["content"] = summary
                state.db.update_grilling(error.id, json.dumps(messages, ensure_ascii=False), summary)
                successmsg("Grilling 思维审讯完成！状态已更新为待讲解")
                return
    except (KeyboardInterrupt, EOFError):
        sysmsg("Grilling 对话已保存（中断）")
        state.db.save_grilling_conversation(error.id, json.dumps(messages, ensure_ascii=False))
        state.db.set_status(error.id, "pending-grill")


def _run_teaching(state: AppState, error):
    if not error.grilling_conversation:
        sysmsg("请先进行 grill 审讯分析")
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

        console.print("[dim]🧠 导师准备讲解中...[/dim]")
        try:
            resp = state.llm.chat(messages)
        except Exception as e:
            errmsg(f"API 错误: {e}")
            return
        console.print(Panel(Markdown(resp), title="[bold green]📖 针对性讲解 (Teach)[/bold green]", border_style="green", padding=(1, 2)))
        messages.append({"role": "assistant", "content": resp})
    else:
        console.print("[dim]正在补全上次的回应...[/dim]")
        try:
            resp = state.llm.chat(messages)
        except Exception as e:
            errmsg(f"API 错误: {e}")
            return
        console.print(Panel(Markdown(resp), title="[bold green]📖 针对性解答[/bold green]", border_style="green", padding=(1, 2)))
        messages.append({"role": "assistant", "content": resp})

    try:
        while True:
            reply = multiline_input("[bold green]💬 提问/讨论（输入「理解了」结束本题讲解）[/bold green]")
            if any(kw in reply for kw in EXIT_KEYWORDS):
                state.db.update_teach(error.id, json.dumps(messages, ensure_ascii=False))
                successmsg("讲解完成！错题已标记为 [已完成]")
                return
            messages.append({"role": "user", "content": reply})
            console.print("[dim]🧠 导师思考中...[/dim]")
            try:
                resp = state.llm.chat(messages)
            except Exception as e:
                errmsg(f"API 错误: {e}")
                state.db.save_teach_conversation(error.id, json.dumps(messages, ensure_ascii=False))
                return
            console.print(Panel(Markdown(resp), title="[bold green]📖 针对性解答[/bold green]", border_style="green", padding=(1, 2)))
            messages.append({"role": "assistant", "content": resp})
    except (KeyboardInterrupt, EOFError):
        sysmsg("讲解对话已保存（中断）")
        state.db.save_teach_conversation(error.id, json.dumps(messages, ensure_ascii=False))


@_register("record", "记录一个 error")
def _cmd_record(state, arg):
    question = popup_input("📝 记录 Error (1/3)", "请粘贴你做错的题目：")
    if not question or not question.strip():
        sysmsg("记录已取消")
        return

    user_thoughts = popup_input("📝 记录 Error (2/3)", "你的思路概述（可留空，留空表示思考过但没做出答案）：")
    if user_thoughts is None:
        sysmsg("记录已取消")
        return

    reference_answer = popup_input("📝 记录 Error (3/3)", "参考答案及解析（不知道可留空）：")
    if reference_answer is None:
        sysmsg("记录已取消")
        return

    error_id = state.db.create_error(
        question=question.strip(),
        user_thoughts=user_thoughts.strip() or None,
        reference_answer=reference_answer.strip() or None,
    )
    successmsg(f"已成功录入错题库 (Error #{error_id})")
    sysmsg("输入 /resume 开始处理错题")


@_register("resume", "查看/处理 error（双栏工作台 → grill/teach）")
def _cmd_resume(state, arg):
    while True:
        errors = state.db.list_all_errors()
        if not errors:
            sysmsg("暂无 error 记录，用 /record 添加")
            return

        idx, action = select_error_split_view(errors)
        if idx is None or action is None:
            return

        error = state.db.get_error(errors[idx].id)
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
                state.db.delete_error(error.id)
                successmsg(f"Error #{idx + 1} 已从错题库删除")


@_register("drill", "出综合练习题")
def _cmd_drill(state, arg):
    n = state.cfg.get("drill_context_n", 10)
    context = state.db.get_drill_context(n)

    if not context:
        sysmsg("暂无可用于出题的 error，请先通过 /resume 完成至少一条 error 的 grilling")
        return

    summary_list = "\n\n".join(
        f"[题目 {i+1}]\n{q}\n[审讯摘要]\n{s}"
        for i, (q, s) in enumerate(context)
    )

    sysmsg("🧠 正在根据近期的 Error Pattern 生成综合演练题...")
    drill_prompt = state.prompts.load("drill.md").format(summary_list=summary_list)
    try:
        result = state.llm.chat_json([{"role": "user", "content": drill_prompt}])
    except Exception as e:
        errmsg(f"生成题目失败: {e}")
        return

    question = result["question"]
    reference_answer = result["reference_answer"]

    user_response = popup_drill_answer(question)
    if not user_response:
        sysmsg("已取消作答")
        return

    sysmsg("⚖️ 判分评估中...")
    judge_prompt = state.prompts.load("judge.md").format(
        question=question,
        reference_answer=reference_answer,
        user_response=user_response,
    )
    try:
        judgment = state.llm.chat_json([{"role": "user", "content": judge_prompt}])
    except Exception as e:
        errmsg(f"判分失败: {e}")
        return

    is_correct = bool(judgment.get("is_correct", False))
    feedback = judgment.get("feedback", "")

    if is_correct:
        popup_content("🎉 回答正确！思维 Pattern 掌握良好！", title="演练评估结果")
        successmsg("回答正确！成功攻克思维盲区！")
    else:
        body = [("bold red", "❌ 答错了，相关 Pattern 仍需巩固\n\n")]
        if feedback:
            body.append(("", f"💡 评估反馈:\n{feedback}\n"))
        popup_content(body, title="演练评估结果")
        errmsg("答错了，已自动将此衍生题作为新 Error 入库")

        new_id = state.db.create_error(
            question=question,
            user_thoughts=user_response,
            reference_answer=reference_answer,
        )
        sysmsg(f"新 error (ID: #{new_id}) 已入库，可随时输入 /resume 处理")


@_register("status", "查看 error 状态统计")
def _cmd_status(state, arg):
    counts = state.db.count_by_status()
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
        ("", "  ⏳ 待审讯 (pending-grill):  "),
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
                _change_provider(state.cfg)
                state.llm = _make_llm(state.cfg)
                save_config(state.cfg)
                successmsg(f"provider 已切换为 {state.cfg['provider']}")
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
        successmsg(f"model 已切换为 {state.cfg.get('model', '?')}")
    except (KeyboardInterrupt, EOFError):
        sysmsg("取消")


@_register("help", "显示此帮助")
def _cmd_help(state, arg):
    groups = [
        ("📝 错题与演练工作流", [
            ("/record", "录入一个新的 error (进入 pending-grill)"),
            ("/resume", "打开双栏工作台 (进行 grill 审讯 / teach 讲解)"),
            ("/drill", "结合近期 error 生成综合演练测试题"),
        ]),
        ("📊 状态与看板", [
            ("/status", "查看错题库状态与完成进度图表看板"),
        ]),
        ("⚙️ 系统与模型配置", [
            ("/config", "调整 drill 条数、grill 轮数或 AI 提供商"),
            ("/model", "快捷切换 AI 模型 (如 gemini-3.5-flash / deepseek-chat)"),
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
