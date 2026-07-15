import json
import re
import sys

from prompt_toolkit import PromptSession
from prompt_toolkit.history import InMemoryHistory
from prompt_toolkit.key_binding import KeyBindings
from rich.console import Console
from rich.panel import Panel

from ..config import load as load_config, save as save_config
from ..db.ops import Database
from ..llm.client import LLMClient, LLMError
from ..llm.gemini import GeminiClient, GeminiError
from ..llm.prompts import PromptManager


console = Console()
PATTERN_ERROR_SUMMARY = re.compile(r"错误类型：(.+)", re.MULTILINE)

_kb = KeyBindings()
@_kb.add("enter")
def _accept(event):
    event.current_buffer.validate_and_handle()

@_kb.add("escape", "enter")
def _newline(event):
    event.current_buffer.insert_text("\n")

_psession = PromptSession(multiline=True, key_bindings=_kb, history=InMemoryHistory())
_sline = PromptSession(history=InMemoryHistory())


def user_input(prompt: str = "", default: str = "") -> str:
    if default:
        console.print(f"{prompt} [dim]({default})[/dim]")
    else:
        console.print(prompt)
    val = _sline.prompt("> ")
    return val if val else default


_hint_shown = False

def multiline_input(prompt: str = "") -> str:
    global _hint_shown
    if not _hint_shown:
        console.print("[dim]提示：Alt+Enter 换行，Enter 提交[/dim]")
        _hint_shown = True
    console.print(prompt)
    return _psession.prompt().strip()

PROVIDERS = {
    "gemini": ("Gemini（谷歌）", GeminiClient, GeminiError),
    "deepseek": ("DeepSeek", LLMClient, LLMError),
}

MODELS = ["gemini-3.5-flash", "gemini-3.1-flash-lite", "gemma-4-26b-a4b-it"]


def _ensure_config():
    cfg = load_config()
    if cfg.get("api_key"):
        return cfg

    try:
        console.print(Panel("[bold]首次使用，请配置 API[/bold]", border_style="blue"))

        provider_keys = list(PROVIDERS.keys())
        console.print("[bold]选择 AI 提供商[/bold]")
        console.print("  1. Gemini（谷歌）")
        console.print("  2. DeepSeek")
        choice = user_input("请输入 1（Gemini）或 2（DeepSeek），回车默认 Gemini", default="1")
        provider = provider_keys[int(choice) - 1]

        cfg["provider"] = provider
        cfg["api_key"] = user_input("输入 API key")

        if provider == "gemini":
            first_models = ["gemini-3.5-flash", "gemini-3.1-flash-lite", "gemma-4-26b-a4b-it"]
            console.print("[bold]选择模型[/bold]")
            for i, m in enumerate(first_models):
                console.print(f"  {i+1}. {m}")
            mc = user_input("请输入 1-3，回车默认 1", default="1")
            cfg["model"] = first_models[int(mc) - 1]

        save_config(cfg)
        console.print(f"[green]配置已保存到 ~/.config/errgrind/config.json[/green]\n")
    except KeyboardInterrupt:
        console.print("\n[yellow]已取消[/yellow]")
        sys.exit(0)
    return cfg


def _make_llm(cfg):
    provider = cfg["provider"]
    info = PROVIDERS[provider]
    api_key = cfg["api_key"]

    if provider == "gemini":
        return info[1](api_key=api_key, model=cfg.get("model", "gemini-3.5-flash"))
    return info[1](api_key=api_key)


def run_session():
    try:
        cfg = _ensure_config()
    except (KeyboardInterrupt, EOFError):
        console.print("\n[yellow]已取消[/yellow]")
        sys.exit(0)

    console.print(f"[dim]使用 {cfg['provider']}[/dim]")
    if cfg["provider"] == "gemini":
        models = ["gemini-3.5-flash", "gemini-3.1-flash-lite", "gemma-4-26b-a4b-it"]
        default_model = cfg.get("model", "gemini-3.5-flash")
        default_idx = models.index(default_model) if default_model in models else 0
        console.print("[bold]选择模型[/bold]")
        for i, m in enumerate(models):
            tag = "（默认）" if m == default_model else ""
            console.print(f"  {i+1}. {m} {tag}")
        choice = user_input(f"请输入 1-{len(models)}，回车默认", default=str(default_idx + 1))
        cfg["model"] = models[int(choice) - 1]
    console.print()

    db = Database()
    llm = _make_llm(cfg)
    prompts = PromptManager()

    try:
        goal = user_input("[bold]你想练习什么？[/bold]（例如：GRE 数学、Python 装饰器、高中物理）")
        console.print("[bold]目标类型[/bold]")
        console.print("  1. 考试提分（兼顾技巧与理解，默认）")
        console.print("  2. 理解性学习（偏重概念深度）")
        goal_type_input = user_input("请输入 1（考试提分）或 2（理解性学习），回车默认考试提分", default="1")
        goal_type = "理解性学习" if goal_type_input == "2" else "考试提分"
        session_id = db.create_session(goal)
        console.print(f"[dim]会话已创建（#{session_id}）[/dim]\n")

        question_text = multiline_input("请粘贴你做错的题目")
        user_answer = multiline_input("你当时选的答案")
        correct_input = multiline_input("正确答案（不知道的话留空）")
        correct_answer = correct_input or "未知（用户不确定）"

        qid = db.save_question(session_id, question_text, correct_answer, source="user_submitted")
        attempt_id = db.save_attempt(session_id, qid, user_answer, is_correct=False)

        while True:
            conversation, error_info = _run_grilling(
                llm, prompts, question_text, user_answer, correct_answer, goal, goal_type
            )
            if error_info:
                categories, summary = error_info
            else:
                categories, summary = "未知", "未能确定错误类型"

            _explain_original(llm, question_text, user_answer, correct_answer, conversation)

            db.save_error_record(
                session_id,
                attempt_id,
                json.dumps(conversation, ensure_ascii=False),
                summary,
                categories,
            )

            result = _drill_question(llm, db, prompts, session_id, goal, question_text, user_answer, summary, categories)
            if result is None:
                break
            question_text, user_answer, correct_answer, attempt_id = result
            console.print(Panel("[bold yellow]答错了，重新开始审讯。[/bold yellow]"))

        console.print(Panel("[bold green]回答正确！本次练习结束。[/bold green]"))
    except KeyboardInterrupt:
        console.print("\n[yellow]已退出[/yellow]")
    except (LLMError, GeminiError) as e:
        console.print(f"[bold red]API 错误: {e}[/bold red]")
    finally:
        db.close()


def _run_grilling(llm, prompts, question_text, user_answer, correct_answer, goal, goal_type):
    goal_file = "goal-exam.md" if goal_type == "考试提分" else "goal-deep.md"
    goal_inst = prompts.load(goal_file)
    system_prompt = prompts.load("grilling.md").format(
        question=question_text,
        user_answer=user_answer,
        correct_answer=correct_answer,
        user_goal=goal,
        goal_type=goal_type,
        goal_instructions=goal_inst,
    )
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": "开始吧"},
    ]

    console.print("[dim]思考中...[/dim]")
    first = llm.chat(messages)
    console.print(Panel(first, title="导师", border_style="cyan"))
    messages.append({"role": "assistant", "content": first})

    if m := PATTERN_ERROR_SUMMARY.search(first):
        return messages, (m.group(1), first)

    for _ in range(20):
        reply = multiline_input("[bold cyan]你的回答[/bold cyan]")
        messages.append({"role": "user", "content": reply})

        console.print("[dim]思考中...[/dim]")
        resp = llm.chat(messages)
        console.print(Panel(resp, title="导师", border_style="cyan"))
        messages.append({"role": "assistant", "content": resp})

        if m := PATTERN_ERROR_SUMMARY.search(resp):
            return messages, (m.group(1), resp)

    return messages, None


def _explain_original(llm, question_text, user_answer, correct_answer, conversation):
    history = "\n".join(
        f"{'导师' if m['role'] == 'assistant' else '用户'}：{m['content']}"
        for m in conversation if m['role'] != 'system'
    )
    prompt = (
        f"基于以下审讯对话，讲解这道题的正确解法。\n\n"
        f"题目：{question_text}\n"
        f"用户错误答案：{user_answer}\n"
        f"正确答案：{correct_answer}\n\n"
        f"审讯对话：\n{history}\n\n"
        f"请指出用户错在哪里，并给出完整的正确解法。"
    )
    console.print("[dim]思考中...[/dim]")
    resp = llm.chat([{"role": "user", "content": prompt}])
    console.print(Panel(resp, title="讲解", border_style="green"))


def _drill_question(llm, db, prompts, session_id, goal, orig_question, user_answer, summary, categories):
    drill_prompt = prompts.load("drill.md").format(
        user_goal=goal,
        compressed_summary=summary,
        original_question=orig_question,
        user_answer=user_answer,
        error_type=categories,
    )
    console.print("[dim]思考中...[/dim]")
    result = llm.chat_json([{"role": "user", "content": drill_prompt}])

    content = result["question"]
    options = result.get("options", [])
    correct = result["correct_answer"]

    qid = db.save_question(
        session_id, content, correct,
        source="ai_generated",
        options=json.dumps(options, ensure_ascii=False),
    )

    console.print(Panel(f"[bold]{content}[/bold]", title="同类题", border_style="yellow"))
    labels = []
    for opt in options:
        label = opt.split(".")[0].strip()
        labels.append(label)
        console.print(f"  {opt}")

    if labels:
        prompt = f"你的选择（{'/'.join([l.upper() for l in labels])}）"
        while True:
            choice = user_input(prompt).strip().upper()
            if choice in [l.upper() for l in labels]:
                break
            console.print(f"[red]请输入 {'/'.join([l.upper() for l in labels])}[/red]")
    else:
        choice = user_input("你的答案")

    is_correct = choice.strip().upper() == correct.strip().upper()
    attempt_id = db.save_attempt(session_id, qid, choice, is_correct)

    if is_correct:
        console.print("[bold green]✓ 回答正确！[/bold green]")
        if "explanation" in result:
            console.print(Panel(result["explanation"], title="解析", border_style="blue"))
        return None
    else:
        console.print(f"[bold red]✗ 正确答案是 {correct}[/bold red]")
        if "explanation" in result:
            console.print(Panel(result["explanation"], title="解析", border_style="blue"))
        return (content, choice, correct, attempt_id)
