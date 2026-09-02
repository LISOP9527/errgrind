import json
import re

from rich.live import Live
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
    render_markdown_to_formatted_text,
    render_markdown_to_plain_text,
    render_terminal_markdown,
)


COMMANDS: dict = {}
COMMAND_DESCRIPTIONS: dict[str, str] = {}


DRILL_SPEC_SCHEMA = {
    "type": "object",
    "properties": {
        "source_error_number": {"type": "integer"},
        "target_pattern": {
            "type": "object",
            "properties": {
                key: {"type": "string"}
                for key in (
                    "mechanism",
                    "trigger",
                    "failure_behavior",
                    "desired_behavior",
                    "success_signal",
                )
            },
            "required": [
                "mechanism",
                "trigger",
                "failure_behavior",
                "desired_behavior",
                "success_signal",
            ],
            "additionalProperties": False,
        },
        "new_problem": {
            "type": "object",
            "properties": {
                "domain": {"type": "string"},
                "task_type": {"type": "string"},
                "setting": {"type": "string"},
                "task_goal": {"type": "string"},
                "essential_trigger": {"type": "string"},
                "solution_strategy": {"type": "string"},
                "avoid": {"type": "array", "items": {"type": "string"}},
            },
            "required": [
                "domain",
                "task_type",
                "setting",
                "task_goal",
                "essential_trigger",
                "solution_strategy",
                "avoid",
            ],
            "additionalProperties": False,
        },
        "difficulty": {
            "type": "object",
            "properties": {
                "level": {"type": "integer"},
                "reasoning_depth": {"type": "integer"},
                "calculation_load": {"type": "integer"},
            },
            "required": ["level", "reasoning_depth", "calculation_load"],
            "additionalProperties": False,
        },
    },
    "required": ["source_error_number", "target_pattern", "new_problem", "difficulty"],
    "additionalProperties": False,
}

DRILL_DRAFT_SCHEMA = {
    "type": "object",
    "properties": {
        "question": {"type": "string"},
        "reference_answer": {"type": "string"},
    },
    "required": ["question", "reference_answer"],
    "additionalProperties": False,
}

JUDGE_SCHEMA = {
    "type": "object",
    "properties": {
        "is_correct": {"type": "boolean"},
        "feedback": {"type": "string"},
    },
    "required": ["is_correct", "feedback"],
    "additionalProperties": False,
}


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


def _llm_chat(messages, llm):
    stream_chat = getattr(llm, "stream_chat", None)
    if not callable(stream_chat):
        with console.status("[bold cyan]🧠 导师思考中...", spinner="dots"):
            return llm.chat(messages)

    collected = []
    with Live(Text("🧠 导师思考中..."), refresh_per_second=15, transient=True) as live:
        def on_token(token: str):
            collected.append(token)
            live.update(render_terminal_markdown("".join(collected)))

        return stream_chat(messages, on_token)


def _is_grilling_complete(response: str) -> bool:
    return response.strip().endswith("[GRILLING_END]")


def _grilling_summary(response: str) -> str:
    return response.strip()[: -len("[GRILLING_END]")].strip()


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
        body = [("class:dim", "暂无 Grilling 对话记录")]
    popup_content(body, title=f"Error #{error.id} - Grilling 记录")


def _run_grilling(state: AppState, error):
    if error.status != "pending-grill":
        _show_grilling_record(error)
        return

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

    if has_conversation and error.id not in state.accessed_error_ids:
        state.accessed_error_ids.add(error.id)
        _show_context_recap(error.grilling_conversation)

    def _get_ai_response() -> bool | None:
        try:
            resp = _llm_chat(messages, state.llm)
        except Exception as e:
            errmsg(f"API 错误: {e}")
            return None
        complete = _is_grilling_complete(resp)
        clean_text = _grilling_summary(resp) if complete else resp
        console.print(Panel(render_terminal_markdown(clean_text), title="[bold cyan]🧠 导师 (Grill)[/bold cyan]", border_style="cyan", padding=(1, 2)))
        messages.append({"role": "assistant", "content": resp})

        if complete:
            summary = _grilling_summary(resp)
            messages[-1]["content"] = summary
            state.db.update_grilling(error.id, json.dumps(messages, ensure_ascii=False), summary)
            successmsg("Grilling 思维审讯完成！状态已更新为待讲解")
            if popup_confirm("Grilling 已完成，是否立即开始讲解？"):
                refreshed_error = state.db.get_error(error.id)
                if refreshed_error is not None:
                    _run_teaching(state, refreshed_error)
        return complete

    try:
        if not has_conversation or (messages and messages[-1]["role"] == "user"):
            complete = _get_ai_response()
            if complete is None or complete:
                return

        max_turns = state.cfg.get("grill_max_turns", 30)
        for _ in range(max_turns):
            reply = multiline_input("[bold cyan]👤 你的回答[/bold cyan]")
            messages.append({"role": "user", "content": reply})

            complete = _get_ai_response()
            if complete is None:
                state.db.save_grilling_conversation(error.id, json.dumps(messages, ensure_ascii=False))
                state.db.set_status(error.id, "pending-grill")
                return
            if complete:
                return
        state.db.save_grilling_conversation(error.id, json.dumps(messages, ensure_ascii=False))
        state.db.set_status(error.id, "pending-grill")
        sysmsg("已达到最大对话轮数，对话已保存")
    except (KeyboardInterrupt, EOFError):
        sysmsg("Grilling 对话已保存（中断）")
        state.db.save_grilling_conversation(error.id, json.dumps(messages, ensure_ascii=False))
        state.db.set_status(error.id, "pending-grill")


def _run_teaching(state: AppState, error):
    if error.status == "pending-grill" or not error.grilling_conversation:
        sysmsg("请先进行 grill 审讯分析")
        return

    messages = json.loads(error.teach_conversation or "[]")
    if error.teach_conversation:
        _show_context_recap(error.teach_conversation)

    if not messages:
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

    try:
        if messages[-1]["role"] == "user":
            title = "[bold green]📖 针对性讲解 (Teach)[/bold green]"
            if error.teach_conversation:
                title = "[bold green]📖 针对性解答[/bold green]"
            console.print("[dim]🧠 导师思考中...[/dim]")
            try:
                resp = state.llm.chat(messages)
            except Exception as e:
                errmsg(f"API 错误: {e}")
                state.db.save_teach_conversation(error.id, json.dumps(messages, ensure_ascii=False))
                return
            console.print(Panel(render_terminal_markdown(resp), title=title, border_style="green", padding=(1, 2)))
            messages.append({"role": "assistant", "content": resp})

        while True:
            reply = multiline_input("[bold green]💬 提问/讨论（Ctrl+C 保存并退出）[/bold green]")
            messages.append({"role": "user", "content": reply})
            console.print("[dim]🧠 导师思考中...[/dim]")
            try:
                resp = state.llm.chat(messages)
            except Exception as e:
                errmsg(f"API 错误: {e}")
                state.db.save_teach_conversation(error.id, json.dumps(messages, ensure_ascii=False))
                return
            console.print(Panel(render_terminal_markdown(resp), title="[bold green]📖 针对性解答[/bold green]", border_style="green", padding=(1, 2)))
            messages.append({"role": "assistant", "content": resp})
    except (KeyboardInterrupt, EOFError):
        state.db.update_teach(error.id, json.dumps(messages, ensure_ascii=False))
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


def _source_leak_terms(source_text):
    folded = source_text.casefold()
    terms = set(re.findall(r"[a-z_][a-z0-9_]{3,}", folded))
    generic_terms = {
        "error",
        "pattern",
        "student",
        "grill",
        "sin",
        "cos",
        "tan",
        "cot",
        "sec",
        "csc",
        "log",
        "sqrt",
    }
    # These describe the workflow or a broad mathematical operation.  They
    # are not source-specific fingerprints and may legitimately reappear in
    # a transfer problem ("pattern" also occurs in the JSON field name).
    terms.difference_update(generic_terms)
    terms.update(re.findall(r"\d{2,}(?:\.\d+)?", folded))
    terms.update(re.findall(r"[\u3400-\u9fff]{4,}", folded))
    for expression in re.findall(r"[a-z0-9_+\-*/^=().°√]+", folded):
        if len(expression) >= 6 and any(op in expression for op in "+-*/^="):
            terms.add(expression)
    return terms


def _required_text(data, key, label):
    value = data[key]
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label}字段 {key} 必须是非空文本")
    return value.strip()


def _required_text_list(data, key, label):
    value = data[key]
    if not isinstance(value, list) or any(
        not isinstance(item, str) or not item.strip() for item in value
    ):
        raise ValueError(f"{label}字段 {key} 必须是文本列表")
    return [item.strip() for item in value]


def _bounded_int(data, key, label, minimum=1, maximum=5):
    value = data[key]
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{label}字段 {key} 必须是整数")
    if not minimum <= value <= maximum:
        raise ValueError(f"{label}字段 {key} 必须在 {minimum}–{maximum} 之间")
    return value


def _normalize_drill_spec(raw_spec, source_records):
    if not isinstance(raw_spec, dict):
        raise ValueError("DrillSpec 必须是 JSON 对象")

    source_number = raw_spec["source_error_number"]
    if isinstance(source_number, bool) or not isinstance(source_number, int):
        raise ValueError("source_error_number 必须是整数")
    if not 1 <= source_number <= len(source_records):
        raise ValueError("source_error_number 超出本次 Error 上下文范围")
    source_index = source_number - 1

    target = raw_spec["target_pattern"]
    new_problem = raw_spec["new_problem"]
    difficulty = raw_spec["difficulty"]
    if not all(isinstance(section, dict) for section in (target, new_problem, difficulty)):
        raise ValueError("DrillSpec 的分组字段必须是 JSON 对象")

    task_types = {
        "calculate",
        "simplify",
        "solve",
        "prove",
        "classify",
        "construct",
        "optimize",
        "explain",
        "determine_truth",
    }
    task_type = _required_text(new_problem, "task_type", "new_problem")
    if task_type not in task_types:
        raise ValueError(f"new_problem.task_type 使用了未知枚举值 {task_type}")

    # 只重建第二阶段真正需要的字段，模型返回的其他内容一律丢弃。
    normalized = {
        "target_pattern": {
            "mechanism": _required_text(target, "mechanism", "target_pattern"),
            "trigger": _required_text(target, "trigger", "target_pattern"),
            "failure_behavior": _required_text(
                target, "failure_behavior", "target_pattern"
            ),
            "desired_behavior": _required_text(
                target, "desired_behavior", "target_pattern"
            ),
            "success_signal": _required_text(
                target, "success_signal", "target_pattern"
            ),
        },
        "new_problem": {
            "domain": _required_text(new_problem, "domain", "new_problem"),
            "task_type": task_type,
            "setting": _required_text(new_problem, "setting", "new_problem"),
            "task_goal": _required_text(new_problem, "task_goal", "new_problem"),
            "essential_trigger": _required_text(
                new_problem, "essential_trigger", "new_problem"
            ),
            "solution_strategy": _required_text(
                new_problem, "solution_strategy", "new_problem"
            ),
            "avoid": _required_text_list(new_problem, "avoid", "new_problem"),
        },
        "difficulty": {
            "level": _bounded_int(difficulty, "level", "difficulty"),
            "reasoning_depth": _bounded_int(
                difficulty, "reasoning_depth", "difficulty"
            ),
            "calculation_load": _bounded_int(
                difficulty, "calculation_load", "difficulty"
            ),
        },
    }

    public_text = json.dumps(normalized, ensure_ascii=False).casefold()
    comparison_terms = ("原题", "旧题", "历史题", "源题", "原始题目")
    if any(term in public_text for term in comparison_terms):
        raise ValueError("公开 DrillSpec 只能正面描述新题，不能引用原题")

    source_texts = [record.question for record in source_records]
    source_texts.extend(
        record.grilling_summary
        for index, record in enumerate(source_records)
        if index != source_index
    )
    leaked_terms = {
        term
        for source_text in source_texts
        for term in _source_leak_terms(source_text)
        if term in public_text
    }
    if leaked_terms:
        examples = "、".join(sorted(leaked_terms, key=len, reverse=True)[:3])
        raise ValueError(f"公开 DrillSpec 仍包含源 Error 特征文本: {examples}")

    return normalized, source_index


def _request_drill_spec(state, error_context, source_records):
    prompt = state.prompts.load("drill_spec.md").format(error_context=error_context)
    messages = [{"role": "user", "content": prompt}]

    for attempt in range(3):
        raw_spec = state.llm.chat_json(messages, output_schema=DRILL_SPEC_SCHEMA)
        try:
            return _normalize_drill_spec(raw_spec, source_records)
        except (KeyError, TypeError, ValueError) as validation_error:
            if attempt == 2:
                raise
            messages.extend([
                {"role": "assistant", "content": json.dumps(raw_spec, ensure_ascii=False)},
                {
                    "role": "user",
                    "content": (
                        f"上次输出不符合 DrillSpec 契约：{validation_error}。"
                        "修复字段、类型或泄漏问题后重新输出完整 JSON；"
                        "不要解释修改过程。"
                    ),
                },
            ])


def _generate_drill(state, drill_spec):
    prompt = state.prompts.load("drill.md").format(
        drill_spec=json.dumps(drill_spec, ensure_ascii=False, indent=2)
    )
    messages = [{"role": "user", "content": prompt}]

    for attempt in range(2):
        result = state.llm.chat_json(messages, output_schema=DRILL_DRAFT_SCHEMA)
        try:
            question = _required_text(result, "question", "出题结果")
            reference_answer = _required_text(
                result, "reference_answer", "出题结果"
            )
            return question, reference_answer
        except (KeyError, TypeError, ValueError) as contract_error:
            if attempt == 1:
                raise ValueError(f"生成题目失败: {contract_error}") from contract_error
            messages.extend([
                {"role": "assistant", "content": json.dumps(result, ensure_ascii=False)},
                {
                    "role": "user",
                    "content": (
                        f"上次出题结果不符合输出契约：{contract_error}。"
                        "只重新输出包含非空 question 和 reference_answer 的完整 JSON。"
                    ),
                },
            ])


@_register("drill", "出综合练习题")
def _cmd_drill(state, arg):
    n = state.cfg.get("drill_context_n", 10)
    context = state.db.get_drill_context(n)

    if not context:
        sysmsg("暂无可用于出题的 error，请先通过 /resume 完成至少一条 error 的 grilling")
        return

    error_context = "\n\n".join(
        f"[Error {i+1}]\n[原题]\n{item.question}\n[Grill 摘要]\n{item.grilling_summary}"
        for i, item in enumerate(context)
    )

    sysmsg("🧠 正在从近期 Error 中提炼出题规格...")
    try:
        drill_spec, source_index = _request_drill_spec(state, error_context, context)
    except Exception as e:
        errmsg(f"提炼出题规格失败: {e}")
        return

    sysmsg("🧩 正在根据出题规格生成新题...")
    try:
        question, reference_answer = _generate_drill(state, drill_spec)
    except Exception as e:
        errmsg(str(e))
        return

    user_response = popup_drill_answer(question)
    if not user_response:
        sysmsg("已取消作答")
        return

    sysmsg("⚖️ 判分评估中...")
    judge_prompt = state.prompts.load("judge.md").format(
        question=question,
        reference_answer=reference_answer,
        user_response=user_response,
        target_pattern=drill_spec["target_pattern"]["mechanism"],
        success_signal=drill_spec["target_pattern"]["success_signal"],
    )
    try:
        judgment = state.llm.chat_json(
            [{"role": "user", "content": judge_prompt}],
            output_schema=JUDGE_SCHEMA,
        )
    except Exception as e:
        errmsg(f"判分失败: {e}")
        return

    is_correct = judgment.get("is_correct")
    if not isinstance(is_correct, bool):
        errmsg("判分失败: is_correct 必须是 JSON 布尔值")
        return

    feedback = judgment.get("feedback")
    if not isinstance(feedback, str):
        errmsg("判分失败: feedback 必须是文本")
        return
    feedback = feedback.strip()

    source_error_id = context[source_index].error_id

    try:
        attempt_result = state.db.record_drill_attempt(
            source_error_id,
            drill_spec,
            question,
            reference_answer,
            user_response,
            is_correct,
            feedback,
        )
    except Exception as error:
        errmsg(f"保存演练结果失败: {error}")
        return

    if is_correct:
        popup_content(
            "本次演练判定为正确。该结果会作为一次干预记录保存，"
            "不等于未来错误已减少。",
            title="演练评估结果",
        )
        successmsg("本次演练判定为正确，结果已记录")
    else:
        body = [("bold red", "❌ 本次演练判定为错误\n\n")]
        if feedback:
            body.append(("class:label", "💡 评估反馈:\n"))
            body.extend(render_markdown_to_formatted_text(feedback))
            body.append(("", "\n"))
        popup_content(body, title="演练评估结果")
        errmsg("答错了，已自动将此衍生题作为新 Error 入库")

        sysmsg(
            f"新 error (ID: #{attempt_result.derived_error_id}) 已入库，"
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
            ("/resume", "打开双栏工作台 (进行 grill 审讯 / teach 讲解)"),
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
