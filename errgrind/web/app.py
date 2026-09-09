"""Local HTTP adapter. All business operations go through ErrGrindApplication."""
from __future__ import annotations

import json
import secrets
import sqlite3
import tempfile
import threading
import time
from collections import OrderedDict
from contextlib import contextmanager
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


class ConfigurationError(Exception):
    pass


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
                      MAX_CONTENT_LENGTH=MAX_IMAGE_BYTES + 1024 * 1024,
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
        return dict(csrf=session['csrf'], token=issue_token, md=md,
                    model_info={k: cfg.get(k) or '默认' for k in ('provider', 'model', 'reasoning_effort')},
                    configured=configured)

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
        return render_template('failure.html', error=text, draft=request.form.to_dict()), code

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
            return render_template('errors.html', errors=api.list_errors())

    @app.get('/record')
    def record():
        return render_template('record.html')

    @app.post('/record')
    @mutation
    def record_save(api):
        q, thoughts = request.form.get('question', ''), request.form.get('user_thoughts', '')
        if not q.strip() or not thoughts.strip():
            abort(400)
        error = api.record_error(q, thoughts, request.form.get('reference_answer'),
                                 origin='ocr' if request.form.get('ocr_used') == '1' else 'record')
        return success(url_for('error_detail', error_id=error.id))

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
        if error is None:
            raise ErrorNotFound()
        return render_template('detail.html', error=error,
                               grill=messages(error.grilling_conversation),
                               teach=messages(error.teach_conversation))

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
            flash('Grill 已暂停，已有对话已保存；之后可继续。')
        elif action == 'finish':
            flash('Teach 已保存并结束，之后仍可继续讨论。')
        return success(url_for('error_detail', error_id=error_id, _anchor=kind))

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
        return render_template('drill.html', key=key, question=prep.question if prep else None,
                               result=result, answer=item['answer'], stage=item['stage'])

    @app.get('/api/drill/<key>')
    def drill_status(key):
        item = get_drill(key)
        return jsonify(stage=item['stage'])

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

    return app
