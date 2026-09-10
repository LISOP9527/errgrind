"""Local HTTP adapter. All business operations go through ErrGrindApplication."""
from __future__ import annotations

import json
import re
import secrets
import sqlite3
import tempfile
import threading
import time
from collections import OrderedDict
from contextlib import contextmanager
from dataclasses import asdict
from functools import wraps
from pathlib import Path
from urllib.parse import urlsplit

from flask import Flask, abort, flash, g, jsonify, redirect, render_template, request, session, url_for
from werkzeug.exceptions import HTTPException

from ..application import (
    ErrGrindApplication, ErrorNotFound, InvalidWorkflowState, NoDrillContext,
    OutputContractError, WorkflowModelError, WorkflowPersistenceError,
)
from ..config import load as load_config, prepare_database_path
from ..db.ops import Database
from ..llm.ocr import MAX_IMAGE_BYTES, OcrError, load_image
from ..llm.prompts import PromptManager
from markupsafe import Markup
from .rendering import render_markdown

def md(value):
    return Markup(render_markdown(value))


def error_title(question: str | None) -> str:
    """Make a compact, deterministic history label from the saved question."""
    value = question or ""
    value = re.sub(r"```.*?```", " ", value, flags=re.DOTALL)
    value = re.sub(r"!\[[^]]*\]\([^)]*\)|\[([^]]+)\]\([^)]*\)", r"\1", value)
    value = re.sub(r"[*_~`#>$\\]+", "", value)
    value = re.sub(r"\s+", " ", value).strip()
    if not value:
        return "未命名错题"
    return value if len(value) <= 54 else value[:53].rstrip() + "…"


def _status_view(status: str) -> tuple[str, str]:
    return {
        "pending-grill": ("待澄清", "attention"),
        "pending-teach": ("可以继续理解", "ready"),
        "done": ("可以继续练习", "quiet"),
    }.get(status, ("可查看", "quiet"))


def _origin_view(origin: str) -> str:
    return {
        "record": "文字录入",
        "ocr": "图片整理",
        "drill": "练习中发现",
        "unknown": "其他来源",
    }.get(origin, "其他来源")


def _history_items(errors):
    items = []
    for error in errors:
        label, tone = _status_view(error.status)
        items.append({
            "id": error.id,
            "title": error_title(error.question),
            "status_label": label,
            "status_tone": tone,
            "created_label": error.created_at.strftime("%m月%d日"),
            "source_label": _origin_view(error.origin),
        })
    return items


def _next_step_view(code: str, error_id: int):
    if code == "start_grill":
        return {
            "code": code,
            "label": "开始澄清思路",
            "description": "用几轮简短回答，把这次错误中实际发生的思考过程说清楚。",
            "href": url_for("conversation", error_id=error_id, kind="grill", action="start"),
        }
    if code == "resume_grill":
        return {
            "code": code,
            "label": "继续澄清思路",
            "description": "已有内容已经保存，可以从上次停下的地方继续。",
            "href": url_for("conversation", error_id=error_id, kind="grill", action="start"),
        }
    if code == "start_teach":
        return {
            "code": code,
            "label": "开始一起理解",
            "description": "基于这次错误的诊断，继续讨论怎样调整自己的思考过程。",
            "href": url_for("conversation", error_id=error_id, kind="teach", action="start"),
        }
    return {
        "code": "start_drill",
        "label": "试做一道新题",
        "description": "用一道新的数学题提供一次练习机会，再看看这个思考方式能否用出来。",
        "href": url_for("drill_new"),
    }


def _drill_state(stage: str) -> str:
    """Collapse private preparation stages into user-facing activity states."""
    return {
        "ready": "idle",
        "spec": "preparing",
        "draft": "preparing",
        "prepared": "answering",
        "judge": "judging",
        "judged": "finished",
        "failed": "failed",
    }.get(stage, "idle")


class ConfigurationError(Exception):
    pass


MAX_RECORD_IMAGES = 3


class _LazyModel:
    """Reading/recording needs no credentials; initialize a provider only on use."""
    def __init__(self, cfg):
        self.cfg, self.client = cfg, None

    def _client(self):
        if self.client is None:
            from ..llm.client import LLMClient, GO_BASE_URL, DEEPSEEK_BASE_URL
            from ..llm.gemini import GeminiClient
            from ..llm.codex import CodexClient
            from ..config import DEFAULT_CODEX_MODEL, DEFAULT_GEMINI_MODEL
            cfg, provider = self.cfg, self.cfg.get('provider')
            if provider not in {'gemini', 'deepseek', 'opencode', 'codex'}:
                raise ConfigurationError()
            if provider != 'codex' and not cfg.get('api_key'):
                raise ConfigurationError()
            if provider == 'codex':
                self.client = CodexClient(model=cfg.get('model') or DEFAULT_CODEX_MODEL,
                                          reasoning_effort=cfg.get('reasoning_effort'))
            elif provider == 'gemini':
                self.client = GeminiClient(api_key=cfg['api_key'], model=cfg.get('model') or DEFAULT_GEMINI_MODEL)
            else:
                self.client = LLMClient(api_key=cfg['api_key'],
                    model=cfg.get('model') or ('deepseek-v4-flash' if provider == 'opencode' else 'deepseek-chat'),
                    base_url=(cfg.get('base_url') or GO_BASE_URL) if provider == 'opencode' else DEEPSEEK_BASE_URL)
        return self.client

    def chat(self, *args, **kwargs):
        return self._client().chat(*args, **kwargs)

    def chat_json(self, *args, **kwargs):
        return self._client().chat_json(*args, **kwargs)

    def transcribe_image(self, *args, **kwargs):
        return self._client().transcribe_image(*args, **kwargs)

    def close(self):
        close = getattr(self.client, 'close', None)
        if callable(close):
            close()


def messages(raw):
    """Only public user/assistant content reaches templates, never system prompts."""
    try:
        items = json.loads(raw or '[]')
        if not isinstance(items, list):
            raise ValueError()
        return [{'role': m['role'], 'content': m['content']} for m in items
                if isinstance(m, dict) and m.get('role') in {'user', 'assistant'}
                and isinstance(m.get('content'), str)]
    except (ValueError, TypeError):
        raise OutputContractError() from None


def create_app(*, db_path=None, cfg=None, llm=None, application_factory=None,
               application=None, secret_key=None):
    app = Flask(__name__)
    if cfg is None:
        try:
            cfg = load_config()
        except (OSError, ValueError, TypeError, AttributeError):
            cfg = {}
    cfg = dict(cfg)
    app.config.update(SECRET_KEY=secret_key or secrets.token_hex(32),
                      MAX_CONTENT_LENGTH=MAX_IMAGE_BYTES * MAX_RECORD_IMAGES + 1024 * 1024,
                      SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE='Strict')
    # Resolve default/migrate legacy path once at startup, not per request.
    path = str(db_path) if db_path is not None else (None if application is not None else prepare_database_path())
    busy, state_lock = threading.Lock(), threading.Lock()
    tokens, drills = OrderedDict(), OrderedDict()

    @contextmanager
    def boundary():
        if application is not None:  # simple single-thread test injection
            yield application
            return
        db = Database(path)
        model = llm if llm is not None else _LazyModel(cfg)
        try:
            yield (application_factory or ErrGrindApplication)(db, model, PromptManager(), cfg)
        finally:
            db.close()
            if llm is None:
                try:
                    model.close()
                except Exception:
                    app.logger.warning('provider cleanup failed')

    def issue_token(purpose):
        value = secrets.token_urlsafe(24)
        with state_lock:
            tokens[value] = (session['sid'], purpose)
            while len(tokens) > 4096:
                tokens.popitem(last=False)
        return value

    def consume_token():
        value = request.form.get('submit_token') or request.headers.get('X-Submission-Token')
        with state_lock:
            expected = tokens.pop(value, None)
        if expected != (session['sid'], request.path):
            abort(409, description='此页面提交已处理或过期，请刷新后再操作。')

    @app.before_request
    def protect():
        session.setdefault('sid', secrets.token_urlsafe(24))
        session.setdefault('csrf', secrets.token_urlsafe(24))
        if request.method == 'POST':
            # CSRF token also covers multipart uploads and browser fetch.
            origin = request.headers.get('Origin')
            if origin and urlsplit(origin).netloc != request.host:
                abort(403)
            submitted = request.form.get('csrf') or request.headers.get('X-CSRFToken', '')
            if not secrets.compare_digest(submitted, session['csrf']):
                abort(403)

    @app.after_request
    def headers(response):
        response.headers['Cache-Control'] = 'no-store'
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['Referrer-Policy'] = 'no-referrer'
        response.headers['Content-Security-Policy'] = (
            "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
            "img-src 'none'; font-src 'self'; connect-src 'self'; object-src 'none'; "
            "base-uri 'none'; frame-ancestors 'none'; form-action 'self'")
        return response

    @app.context_processor
    def common():
        configured = cfg.get('provider') == 'codex' or bool(cfg.get('api_key')) or llm is not None or application is not None
        try:
            with boundary() as api:
                history_items = _history_items(api.list_errors())
        except Exception:
            # A read-only sidebar must not hide the recovery page when the
            # database itself is the operation that failed.
            app.logger.warning('history load failed while rendering page')
            history_items = []
        return dict(csrf=session['csrf'], token=issue_token, md=md,
                    model_info={k: cfg.get(k) or '默认' for k in ('provider', 'model', 'reasoning_effort')},
                    configured=configured, history_items=history_items,
                    current_error_id=None)

    def wants_json():
        return request.path.startswith('/api/') or '/ocr/' in request.path or request.accept_mimetypes.best == 'application/json'

    @app.errorhandler(Exception)
    def failure(exc):
        code, category, text = 500, 'internal', '操作未完成。请保留当前输入，稍后重试。'
        if isinstance(exc, ErrorNotFound):
            code, category, text = 404, 'not_found', '找不到这条记录或练习，可能已删除或过期。'
        elif isinstance(exc, NoDrillContext):
            code, category, text = 409, 'invalid_state', '暂无可用于出题的诊断，请先完成一条有足够机制证据的 Grill。'
        elif isinstance(exc, InvalidWorkflowState):
            code, category, text = 409, 'invalid_state', '当前状态不能执行此操作。请查看已保存对话，开始或恢复相应阶段。'
        elif isinstance(exc, OutputContractError):
            code, category, text = 422, 'output_contract', '模型输出未通过校验，本次没有展示未校验结果。可以稍后恢复或重试。'
        elif isinstance(exc, (WorkflowModelError, ConfigurationError)):
            code, category, text = 502, 'model', 'Provider/model 请求失败（可能是限流 429、配置、登录或网络问题）。请检查 CLI 配置，稍后恢复或重试。'
        elif isinstance(exc, (WorkflowPersistenceError, sqlite3.Error, OSError)):
            code, category, text = 503, 'persistence', '数据库或文件保存失败，无法确认本次输入已保存。请保留输入并查看记录后再重试。'
        elif isinstance(exc, OcrError):
            code, category, text = 400, 'invalid_input', '请选择有效的 PNG、JPEG 或 WebP 图片，每张不超过 20 MB。'
        elif isinstance(exc, HTTPException):
            code = exc.code
            category = {404: 'not_found', 409: 'duplicate', 400: 'invalid_input', 413: 'invalid_input', 403: 'forbidden'}.get(code, 'request')
            text = {404: '找不到这条记录或练习，可能已删除或过期。',
                    409: '已有操作正在处理，或本次提交已处理/过期。请等待或查看记录后再操作。',
                    400: '请检查输入；题目、思路或当前回答不能为空。',
                    413: '上传文件过大，每张图片上限 20 MB。',
                    403: '请求验证失败，请刷新页面后重试。'}.get(code, '请求未完成，请刷新页面后重试。')
        # Never log exception values/chains: providers may embed bodies or tokens.
        app.logger.warning('web failure category=%s status=%s', category, code)
        if getattr(g, 'conversation_action', False) and category in {'model', 'output_contract'}:
            text += ' 已接收的对话输入仍在 SQLite 中；请使用“开始 / 恢复”，不要重复发送同一回答。'
        elif getattr(g, 'judge_action', False):
            text += ' 答案草稿保留在当前页面/进程中；尚未确认写入判分记录。'
        payload = dict(error=text, category=category, submit_token=issue_token(request.path))
        if wants_json():
            return jsonify(payload), code
        recovery_url = url_for('index')
        if request.endpoint == 'conversation' and request.view_args:
            recovery_url = url_for('error_detail', error_id=request.view_args['error_id'])
        elif request.endpoint in {'drill_prepare', 'drill_judge', 'drill_ocr'} and request.view_args:
            recovery_url = url_for('drill_detail', key=request.view_args['key'])
        elif request.endpoint in {'record_save', 'record_ocr', 'record_draft'}:
            recovery_url = url_for('record')
        return render_template(
            'failure.html',
            error=text,
            draft=request.form.to_dict(),
            return_url=recovery_url,
        ), code

    def mutation(fn):
        @wraps(fn)
        def wrapped(*args, **kwargs):
            consume_token()
            if not busy.acquire(blocking=False):
                abort(409)
            try:
                with boundary() as api:
                    return fn(api, *args, **kwargs)
            finally:
                busy.release()
        return wrapped

    def success(location):
        if wants_json():
            return jsonify(redirect=location)
        return redirect(location, code=303)

    def get_drill(key):
        with state_lock:
            item = drills.get(key)
            if item is None or item['owner'] != session['sid']:
                raise ErrorNotFound()
            return item

    @app.get('/')
    def index():
        with boundary() as api:
            has_errors = bool(api.list_errors())
        return render_template('errors.html', has_errors=has_errors)

    @app.get('/record')
    def record():
        return render_template('record.html')

    @app.get('/config')
    def config():
        return render_template('config.html')

    @app.post('/record')
    @mutation
    def record_save(api):
        q, thoughts = request.form.get('question', ''), request.form.get('user_thoughts', '')
        if not q.strip() or not thoughts.strip():
            abort(400)
        error = api.record_error(q, thoughts, request.form.get('reference_answer'),
                                 origin='ocr' if request.form.get('ocr_used') == '1' else 'record')
        return success(url_for('error_detail', error_id=error.id))

    @app.post('/api/record/draft')
    @mutation
    def record_draft(api):
        uploads = [item for item in request.files.getlist('images') if item.filename]
        if len(uploads) > MAX_RECORD_IMAGES:
            abort(400)
        raw_input = request.form.get('raw_input', '')
        with tempfile.TemporaryDirectory(prefix='errgrind-web-') as tmp:
            paths = []
            for index, upload in enumerate(uploads):
                filename = str(Path(tmp) / f'upload-{index}')
                upload.save(filename)
                load_image(filename)
                paths.append(filename)
            draft = api.prepare_record_draft(raw_input, paths)
        return jsonify(draft=asdict(draft), submit_token=issue_token(request.path))

    @app.post('/record/ocr/<field>')
    @mutation
    def record_ocr(api, field):
        if field not in {'question', 'user_thoughts', 'reference_answer'}:
            abort(400)
        upload = request.files.get('image')
        if upload is None or not upload.filename:
            abort(400)
        # Fixed filename: browser filenames never become server paths.
        with tempfile.TemporaryDirectory(prefix='errgrind-web-') as tmp:
            filename = str(Path(tmp) / 'upload')
            upload.save(filename)
            load_image(filename)
            text = api.transcribe_record_field(filename, field)
        return jsonify(text=text, submit_token=issue_token(request.path))

    @app.get('/errors/<int:error_id>')
    def error_detail(error_id):
        with boundary() as api:
            error = api.get_error(error_id)
            next_step = api.get_next_step(error_id)
        if error is None:
            raise ErrorNotFound()
        step = _next_step_view(next_step.code, error_id)
        return render_template('detail.html', error=error,
                               grill=messages(error.grilling_conversation),
                               teach=messages(error.teach_conversation),
                               error_title=error_title(error.question),
                               origin_label=_origin_view(error.origin),
                               next_step=step,
                               current_error_id=error.id)

    @app.post('/errors/<int:error_id>/<kind>/<action>')
    @mutation
    def conversation(api, error_id, kind, action):
        operations = {
            ('grill', 'start'): api.start_or_resume_grill,
            ('grill', 'answer'): api.submit_grill_answer,
            ('grill', 'pause'): api.pause_grill,
            ('teach', 'start'): api.start_or_resume_teach,
            ('teach', 'answer'): api.submit_teach_answer,
            ('teach', 'finish'): api.finish_teach,
        }
        operation = operations.get((kind, action))
        if operation is None:
            abort(404)
        args = [error_id]
        if action == 'answer':
            answer = request.form.get('answer', '')
            if not answer.strip():
                abort(400)
            args.append(answer)
        g.conversation_action = action in {'start', 'answer'}
        operation(*args)
        if action == 'pause':
            flash('已有对话已保存，之后可以继续。')
        elif action == 'finish':
            flash('本次讨论已保存，之后仍可继续。')
        return success(url_for('error_detail', error_id=error_id, _anchor='composer'))

    @app.get('/drill')
    def drill_new():
        key = secrets.token_urlsafe(24)
        with state_lock:
            drills[key] = dict(owner=session['sid'], stage='ready', preparation=None, judgment=None, answer='')
            while len(drills) > 32:
                removable = next((k for k, v in drills.items() if v['stage'] not in {'spec', 'draft', 'judge'}), None)
                if removable is None:
                    abort(409)
                drills.pop(removable)
        return redirect(url_for('drill_detail', key=key), code=303)

    @app.get('/drill/<key>')
    def drill_detail(key):
        item = get_drill(key)
        # Explicit whitelist. Neither preparation nor drill_spec reaches Jinja.
        prep, result = item['preparation'], item['judgment']
        public_result = None
        if result is not None:
            public_result = {
                'is_correct': result.is_correct,
                'derived_error_id': result.attempt.derived_error_id,
            }
        return render_template('drill.html', key=key, question=prep.question if prep else None,
                               result=public_result, answer=item['answer'], state=_drill_state(item['stage']))

    @app.get('/api/drill/<key>')
    def drill_status(key):
        item = get_drill(key)
        return jsonify(state=_drill_state(item['stage']))

    @app.post('/api/drill/<key>/prepare')
    @mutation
    def drill_prepare(api, key):
        item = get_drill(key)
        if item['preparation'] is not None:
            return success(url_for('drill_detail', key=key))
        def on_stage(stage):
            with state_lock:
                item['stage'] = stage.value
        try:
            item['preparation'] = api.prepare_drill(on_stage=on_stage)
            item['stage'] = 'prepared'
        except Exception:
            item['stage'] = 'failed'
            raise
        return success(url_for('drill_detail', key=key))

    @app.post('/api/drill/<key>/judge')
    @mutation
    def drill_judge(api, key):
        item = get_drill(key)
        if item['preparation'] is None:
            raise InvalidWorkflowState()
        if item['judgment'] is not None:
            return success(url_for('drill_detail', key=key))
        answer = request.form.get('answer', '')
        if not answer.strip():
            abort(400)
        item['answer'], item['stage'] = answer, 'judge'
        g.judge_action = True
        try:
            item['judgment'] = api.judge_and_record_drill(item['preparation'], answer)
            item['stage'] = 'judged'
        except Exception:
            item['stage'] = 'prepared'
            raise
        return success(url_for('drill_detail', key=key))

    @app.post('/api/drill/<key>/ocr')
    @mutation
    def drill_ocr(api, key):
        item = get_drill(key)
        if item['preparation'] is None or item['judgment'] is not None:
            raise InvalidWorkflowState('当前练习不能再修改答案')
        upload = request.files.get('image')
        if upload is None or not upload.filename:
            abort(400)
        with tempfile.TemporaryDirectory(prefix='errgrind-web-') as tmp:
            filename = str(Path(tmp) / 'upload')
            upload.save(filename)
            load_image(filename)
            text = api.transcribe_drill_answer(filename)
        existing = request.form.get('answer', item['answer'])
        if not isinstance(existing, str):
            existing = ''
        item['answer'] = existing + ('\n\n' if existing.strip() else '') + text
        return jsonify(text=text, answer=item['answer'], submit_token=issue_token(request.path))

    return app
