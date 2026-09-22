"""Local HTTP adapter. All business operations go through ErrGrindApplication."""
from __future__ import annotations

import gzip
import json
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

from flask import Flask, Response, abort, flash, g, jsonify, redirect, render_template, request, send_from_directory, session, url_for
from werkzeug.exceptions import HTTPException

from ..application import (
    ErrGrindApplication, ErrorNotFound, InvalidWorkflowState, NoDrillContext,
    OutputContractError, WorkflowModelError, WorkflowPersistenceError,
)
from ..config import (
    DEFAULT_CODEX_MODEL, DEFAULT_GEMINI_MODEL,
    load as load_config, prepare_database_path, save as save_config,
)
from ..db.ops import Database
from ..llm.client import GO_BASE_URL
from ..llm.catalog import ModelCatalogError, discover_models
from ..llm.ocr import MAX_IMAGE_BYTES, OcrError, load_image
from ..llm.prompts import PromptManager
from markupsafe import Markup
from ..models.titles import context_excerpt, display_title_for_question, is_safe_display_title
from .rendering import render_markdown

def md(value):
    return Markup(render_markdown(value))


def error_title(question: str | None) -> str:
    """Legacy-friendly title fallback for records without a stored title."""
    return display_title_for_question(question)


def _display_title(error) -> str:
    """Use a persisted title only after the deterministic safety check."""
    if is_safe_display_title(error.display_title):
        return error.display_title.strip()
    return error_title(error.question)


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
        items.append({
            "id": error.id,
            "title": _display_title(error),
            # The dot is intentionally the only history affordance for the
            # current workflow status.  The selected-row background is a
            # separate presentation concern handled by the template.
            "status_dot": {
                "pending-grill": "red",
                "pending-teach": "amber",
            }.get(error.status),
        })
    return items


def _next_action_view(code: str, error_id: int, *, teach_active: bool):
    if code == "start_grill":
        return {
            "code": code,
            "label": "Grill",
            "description": "先还原当时的思路，确认这次错误是怎么发生的。",
            "method": "post",
            "href": url_for("conversation", error_id=error_id, kind="grill", action="start"),
        }
    if code == "resume_grill":
        return {
            "code": code,
            "label": "Grill",
            "description": "继续上次的诊断，把这次错误的原因确认清楚。",
            "method": "post",
            "href": url_for("conversation", error_id=error_id, kind="grill", action="start"),
        }
    if teach_active:
        return {
            "code": "finish_teach_and_drill",
            "label": "Drill",
            "description": "继续追问，或针对当前 Error 做一道新练习。",
            "method": "post",
            "href": url_for("teach_to_drill", error_id=error_id),
        }
    if code == "start_teach":
        return {
            "code": code,
            "label": "Teach",
            "description": "诊断已经完成，继续理解并修正这次错误。",
            "method": "post",
            "href": url_for("conversation", error_id=error_id, kind="teach", action="start"),
        }
    return {
        "code": "start_drill",
        "label": "Drill",
        "description": "针对当前 Error 做一道新练习。",
        "method": "get",
        "href": url_for("drill_for_error", error_id=error_id),
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


def _record_draft_status(draft, *, attachment_count=0):
    """Describe draft readiness without spending another model call."""
    missing_fields = [] if attachment_count else list(draft.missing_fields)
    if missing_fields:
        return {
            "ready": False,
            "status": "incomplete",
            "missing_fields": missing_fields,
            "message": "还缺少题目；请继续补充题目，参考答案和当时的思路可以留空。",
        }
    return {
        "ready": True,
        "status": "ready",
        "missing_fields": [],
        "message": "草稿已具备题目，可以继续调整或确认保存。",
    }


class ConfigurationError(Exception):
    pass


MAX_RECORD_IMAGES = 3
PROVIDER_MODELS = {
    'gemini': DEFAULT_GEMINI_MODEL,
    'deepseek': 'deepseek-chat',
    'opencode': 'deepseek-v4-flash',
    'codex': DEFAULT_CODEX_MODEL,
}
CODEX_EFFORTS = {'low', 'medium', 'high', 'xhigh', 'max', 'ultra'}


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

    def close(self):
        close = getattr(self.client, 'close', None)
        if callable(close):
            close()


def messages(raw, *, hide_grill_bootstrap: bool = False):
    """Only public user/assistant content reaches templates, never system prompts."""
    try:
        items = json.loads(raw or '[]')
        if not isinstance(items, list):
            raise ValueError()
        # The first Grill user turn is an implementation bootstrap, not a
        # meaningful user message.  Filter this exact persisted shape only;
        # later user messages with the same text remain part of the history.
        public_items = [
            {
                'role': m['role'],
                'content': m['content'],
                'attachment_ids': list(m.get('attachments', [])),
                'attachment_count': len(m.get('attachments', []))
                if isinstance(m.get('attachments', []), list)
                else 0,
            }
            for m in items
            if isinstance(m, dict) and m.get('role') in {'user', 'assistant'}
            and isinstance(m.get('content'), str)
            and isinstance(m.get('attachments', []), list)
        ]
        if hide_grill_bootstrap and public_items:
            first = public_items[0]
            if first['role'] == 'user' and first['content'] == '开始吧':
                public_items = public_items[1:]
        return public_items
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
        if request.path.startswith('/static/assistant-ui/assets/'):
            return None
        session.setdefault('sid', secrets.token_urlsafe(24))
        session.setdefault('csrf', secrets.token_urlsafe(24))
        if request.method == 'POST':
            # CSRF token also covers multipart uploads and browser fetch.
            origin = request.headers.get('Origin')
            # Chromium sends Origin: null for a same-origin HTML form when
            # Referrer-Policy is no-referrer. Fetch Metadata distinguishes it
            # from an opaque cross-site origin; CSRF still applies below.
            same_origin_null = (origin == 'null' and
                                request.headers.get('Sec-Fetch-Site') == 'same-origin')
            if origin and not same_origin_null and urlsplit(origin).netloc != request.host:
                abort(403)
            submitted = request.form.get('csrf') or request.headers.get('X-CSRFToken', '')
            if not secrets.compare_digest(submitted, session['csrf']):
                abort(403)

    @app.after_request
    def headers(response):
        asset = request.path.startswith('/static/assistant-ui/assets/')
        if asset:
            response.headers['Cache-Control'] = 'public, max-age=31536000, immutable'
            content_type = response.mimetype or ''
            if (
                request.method != 'HEAD'
                and 'gzip' in request.headers.get('Accept-Encoding', '').lower()
                and response.status_code == 200
                and response.headers.get('Content-Encoding') is None
                and (content_type.startswith('text/') or content_type in {
                    'application/javascript', 'text/javascript', 'application/json'
                })
            ):
                response.direct_passthrough = False
                data = response.get_data()
                if len(data) >= 1024:
                    response.set_data(gzip.compress(data, compresslevel=6))
                    response.headers['Content-Encoding'] = 'gzip'
                    response.headers['Vary'] = 'Accept-Encoding'
        else:
            response.headers['Cache-Control'] = 'no-store'
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['Referrer-Policy'] = 'no-referrer'
        response.headers['Content-Security-Policy'] = (
            "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
            "img-src 'self' blob:; font-src 'self'; connect-src 'self'; object-src 'none'; "
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
                    context_excerpt=context_excerpt,
                    model_info={k: cfg.get(k) or '默认' for k in ('provider', 'model', 'reasoning_effort')},
                    configured=configured, history_items=history_items,
                    current_error_id=None)

    def wants_json():
        return request.path.startswith('/api/') or request.accept_mimetypes.best == 'application/json'

    def request_value(name, default=''):
        """Read scalar API input from either form data or a JSON object."""
        value = request.form.get(name)
        if value is not None:
            return value
        payload = request.get_json(silent=True)
        return payload.get(name, default) if isinstance(payload, dict) else default

    def request_int(name):
        value = request_value(name, None)
        try:
            return int(value)
        except (TypeError, ValueError):
            abort(400)

    def public_error_payload(error):
        """Keep the assistant API independent of the internal Error dataclass."""
        return {
            field: getattr(error, field)
            for field in (
                'id', 'origin', 'question',
                'user_thoughts', 'reference_answer',
            )
        }

    def public_grill_payload(result, *, submit_token):
        public_messages = []
        for message in result.messages:
            if not isinstance(message, dict) or message.get('role') not in {'user', 'assistant'}:
                continue
            content = message.get('content')
            attachments = message.get('attachments', [])
            if not isinstance(content, str) or not isinstance(attachments, list):
                continue
            public_messages.append({
                'role': message['role'],
                'content': content,
                'attachment_ids': list(attachments),
                'attachment_count': len(attachments),
            })
        return {
            'messages': public_messages,
            'error': public_error_payload(result.error),
            'assistant_response': result.assistant_response,
            'summary': result.summary,
            'submit_token': submit_token,
        }

    def attachment_view(api, error_id, attachment_id):
        """Build a same-origin image URL after the application ownership check."""
        attachment = api.get_error_attachment(error_id, attachment_id)
        if attachment is None:
            raise ErrorNotFound()
        return {
            'id': attachment.id,
            'url': url_for('assistant_attachment', error_id=error_id,
                           attachment_id=attachment.id),
            'mime_type': attachment.mime_type,
        }

    def public_messages(api, error_id, raw, *, hide_grill_bootstrap=False,
                        conversation_kind=None):
        result = []
        for item in messages(raw, hide_grill_bootstrap=hide_grill_bootstrap):
            attachments = []
            for attachment_id in item.get('attachment_ids', []):
                try:
                    attachments.append(attachment_view(api, error_id, int(attachment_id)))
                except (TypeError, ValueError):
                    raise OutputContractError() from None
            result.append({
                'role': item['role'],
                'content': item['content'],
                'attachments': attachments,
            })
        return result

    def public_workspace(api, error_id):
        """Return the public Error conversation without private diagnosis state."""
        error = api.get_error(error_id)
        if error is None:
            raise ErrorNotFound()
        initial_attachments = [
            attachment_view(api, error_id, item.id)
            for item in api.list_error_attachments(error_id, conversation_kind='initial')
        ]
        initial_parts = [f'题目\n{error.question}']
        if error.user_thoughts:
            initial_parts.append(f'当时的作答 / 思路\n{error.user_thoughts}')
        conversation = [{
            'role': 'user',
            'content': '\n\n'.join(initial_parts),
            'attachments': initial_attachments,
        }]
        grill = public_messages(
            api, error_id, error.grilling_conversation,
            hide_grill_bootstrap=True, conversation_kind='grill',
        )
        if (grill and error.grilling_summary
                and grill[-1]['role'] == 'assistant'
                and grill[-1]['content'] == error.grilling_summary):
            grill = grill[:-1]
        conversation.extend(grill)
        if error.grilling_summary:
            conversation.append({
                'role': 'assistant',
                'content': error.grilling_summary,
                'attachments': [],
            })
        conversation.extend(public_messages(
            api, error_id, error.teach_conversation,
            conversation_kind='teach',
        ))

        grill_raw = messages(error.grilling_conversation, hide_grill_bootstrap=True)
        teach_raw = messages(error.teach_conversation)
        grill_needs_answer = (
            error.status == 'pending-grill' and grill_raw
            and grill_raw[-1]['role'] == 'assistant'
        )
        teach_needs_answer = (
            error.status == 'pending-teach' and teach_raw
            and teach_raw[-1]['role'] == 'assistant'
        )
        teach_retry_needed = (
            error.status == 'pending-teach' and teach_raw
            and teach_raw[-1]['role'] == 'user'
        )
        next_step = api.get_next_step(error_id)
        step = None if grill_needs_answer else _next_action_view(
            next_step.code,
            error_id,
            teach_active=(
                error.status == 'pending-teach'
                and bool(teach_raw)
                and not teach_retry_needed
            ),
        )
        composer = 'grill' if grill_needs_answer else 'teach' if teach_needs_answer else None
        return {
            'error': {
                'id': error.id,
                'title': _display_title(error),
                'origin': _origin_view(error.origin),
                'created_at': error.created_at.isoformat(),
                'source_error_id': error.source_error_id,
                'question': error.question,
                'user_thoughts': error.user_thoughts,
                'reference_answer': error.reference_answer,
                'initial_attachments': initial_attachments,
            },
            'messages': conversation,
            'composer': composer,
            'next_step': (None if step is None else {
                'code': step['code'],
                'label': step['label'],
                'description': step['description'],
            }),
        }

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
            code, category, text = 502, 'model', 'Provider/model 请求失败（可能是限流 429、配置、登录或网络问题）。请检查 Settings，稍后恢复或重试。'
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
        if request.endpoint in {'conversation', 'teach_to_drill'} and request.view_args:
            recovery_url = url_for('error_detail', error_id=request.view_args['error_id'])
        elif request.endpoint in {'drill_prepare', 'drill_judge'} and request.view_args:
            recovery_url = url_for('drill_detail', key=request.view_args['key'])
        elif request.endpoint in {'record_save', 'record_draft'}:
            recovery_url = url_for('record')
        return render_template(
            'failure.html',
            error=text,
            # A failed settings request must never echo a submitted API key.
            draft={key: value for key, value in request.form.items() if key != 'api_key'},
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

    @contextmanager
    def uploaded_image_paths(field='images', limit=MAX_RECORD_IMAGES):
        """Validate uploads into server-owned temporary names only."""
        uploads = [item for item in request.files.getlist(field) if item.filename]
        if len(uploads) > limit:
            abort(400)
        with tempfile.TemporaryDirectory(prefix='errgrind-web-') as tmp:
            paths = []
            for index, upload in enumerate(uploads):
                filename = str(Path(tmp) / f'upload-{index}')
                upload.save(filename)
                load_image(filename)
                paths.append(filename)
            yield paths

    def record_pending_key():
        key = session.get('record_pending_key')
        if not isinstance(key, str) or not key:
            key = 'record:' + session['sid'] + ':' + secrets.token_urlsafe(18)
            session['record_pending_key'] = key
        return key

    assistant_dist = Path(app.static_folder) / 'assistant-ui'

    def legacy_body(html):
        """Keep a non-JavaScript recovery/testing projection outside React's root."""
        start = html.find('<body')
        start = html.find('>', start) + 1 if start >= 0 else 0
        end = html.rfind('</body>')
        body = html[start:end if end >= 0 else None]
        while '<script' in body:
            script_start = body.find('<script')
            script_end = body.find('</script>', script_start)
            if script_end < 0:
                body = body[:script_start]
                break
            body = body[:script_start] + body[script_end + len('</script>'):]
        return body

    def assistant_shell(*, legacy_html=None, status=200):
        """Serve the package-local production React shell with a hidden no-JS fallback."""
        shell = (assistant_dist / 'index.html').read_text(encoding='utf-8')
        if legacy_html is None:
            error_id = request.view_args.get('error_id') if request.view_args else None
            if error_id is None:
                with boundary() as api:
                    legacy_html = render_template('errors.html', has_errors=bool(api.list_errors()))
            else:
                legacy_html = legacy_error_detail(error_id)
        fallback = legacy_body(legacy_html)
        shell = shell.replace('</body>', '<div id="legacy-compat" hidden>' + fallback + '</div></body>')
        return Response(shell, status=status, mimetype='text/html')

    @app.get('/')
    def index():
        return assistant_shell()

    @app.get('/record')
    def record():
        return redirect(url_for('index'))

    @app.get('/config')
    def config():
        legacy, status = render_config()
        return assistant_shell(legacy_html=legacy, status=status)

    def config_payload(*, values=None, form_error=None):
        settings = dict(values if values is not None else cfg)
        settings.pop('api_key', None)
        settings.setdefault('provider', 'gemini')
        settings.setdefault('model', PROVIDER_MODELS.get(settings['provider'], ''))
        settings.setdefault('reasoning_effort', None)
        settings.setdefault('drill_context_n', 10)
        settings.setdefault('grill_max_turns', 30)
        settings.setdefault('base_url', cfg.get('base_url', '') if settings['provider'] == 'opencode' else '')
        return {
            'settings': settings,
            'form_error': form_error,
            'api_key_set': bool(cfg.get('api_key')) and cfg.get('provider') == settings['provider'],
            'provider_models': PROVIDER_MODELS,
            'opencode_base_url': GO_BASE_URL,
        }

    @app.get('/api/assistant/config')
    def assistant_config():
        return jsonify(config_payload())

    @app.post('/api/assistant/models')
    def assistant_models():
        """Discover provider models without persisting the submitted key."""
        values = request.get_json(silent=True) or request.form
        if not hasattr(values, 'get'):
            return jsonify(error='模型目录请求格式无效。'), 400

        def text_field(name):
            value = values.get(name, '')
            return value.strip() if isinstance(value, str) else ''

        provider = text_field('provider')
        submitted_key = text_field('api_key')
        base_url = text_field('base_url')
        if provider not in PROVIDER_MODELS:
            return jsonify(error='请选择有效的 AI 提供商。'), 400
        if len(submitted_key) > 4096 or len(base_url) > 2048:
            return jsonify(error='API Key 或 API 地址过长。'), 400
        using_saved_key = False
        if provider == 'codex':
            submitted_key, base_url = '', ''
        elif not submitted_key and provider == cfg.get('provider'):
            submitted_key = cfg.get('api_key', '')
            using_saved_key = bool(submitted_key)
        if provider == 'deepseek':
            base_url = ''
        elif provider == 'opencode':
            saved_base_url = (cfg.get('base_url') or GO_BASE_URL).strip().rstrip('/')
            requested_base_url = (base_url or (
                saved_base_url if provider == cfg.get('provider') else GO_BASE_URL
            )).strip().rstrip('/')
            if using_saved_key and requested_base_url != saved_base_url:
                return jsonify(
                    error='修改 OpenCode API 地址时，请重新输入 API Key。'
                ), 400
            base_url = requested_base_url
        if provider != 'codex' and not submitted_key:
            return jsonify(error='切换提供商后，请先输入新的 API Key。'), 400
        try:
            models = discover_models(provider, api_key=submitted_key, base_url=base_url)
        except ModelCatalogError as error:
            app.logger.warning('model catalog discovery failed for provider %s', provider)
            return jsonify(error=str(error)), 502
        if not models:
            return jsonify(error='当前账号的模型目录没有返回可用模型。'), 502
        return jsonify(provider=provider, models=models)

    def render_config(*, values=None, form_error=None, status=200):
        payload = config_payload(values=values, form_error=form_error)
        return render_template('config.html', **payload), status

    @app.post('/config')
    def config_save():
        nonlocal cfg
        consume_token()
        if not busy.acquire(blocking=False):
            abort(409)
        try:
            provider = request.form.get('provider', '').strip()
            model = request.form.get('model', '').strip()
            effort = request.form.get('reasoning_effort', '').strip()
            key = request.form.get('api_key', '').strip()
            base_url = request.form.get('base_url', '').strip()
            context_n = request.form.get('drill_context_n', '').strip()
            max_turns = request.form.get('grill_max_turns', '').strip()
            values = dict(provider=provider, model=model, reasoning_effort=effort,
                          base_url=base_url, drill_context_n=context_n,
                          grill_max_turns=max_turns)

            def invalid(message):
                if wants_json():
                    return jsonify(error=message), 400
                return render_config(values=values, form_error=message, status=400)

            if provider not in PROVIDER_MODELS:
                return invalid('请选择有效的 AI 提供商。')
            if len(model) > 200 or len(key) > 4096 or len(base_url) > 2048:
                return invalid('模型 ID、API Key 或 API 地址过长。')
            if provider == 'codex':
                if effort and effort not in CODEX_EFFORTS:
                    return invalid('请选择有效的 Codex reasoning effort。')
            else:
                effort = ''
            try:
                context_n, max_turns = int(context_n), int(max_turns)
            except ValueError:
                return invalid('上下文条数和最大轮数必须是整数。')
            if context_n < 1 or max_turns < 1:
                return invalid('上下文条数和最大轮数必须至少为 1。')

            changed_provider = provider != cfg.get('provider')
            if not model or (changed_provider and model == cfg.get('model')):
                model = PROVIDER_MODELS[provider]
            candidate = dict(cfg)
            candidate.update(provider=provider, model=model,
                             reasoning_effort=effort or None,
                             drill_context_n=context_n, grill_max_turns=max_turns)
            if provider == 'codex':
                candidate.pop('api_key', None)
                candidate.pop('base_url', None)
            else:
                if key:
                    candidate['api_key'] = key
                elif changed_provider or request.form.get('clear_api_key') == '1':
                    candidate['api_key'] = ''
                if changed_provider and not candidate.get('api_key'):
                    return invalid('切换提供商时，请输入新提供商的 API Key。')
                if provider == 'opencode':
                    base_url = base_url or GO_BASE_URL
                    try:
                        parsed = urlsplit(base_url)
                        valid_url = (parsed.scheme in {'http', 'https'} and bool(parsed.hostname)
                                     and not parsed.username and not parsed.password)
                    except ValueError:
                        valid_url = False
                    if not valid_url:
                        return invalid('请输入有效的 OpenCode API 地址（http 或 https）。')
                    old_base_url = (cfg.get('base_url') or GO_BASE_URL).strip().rstrip('/')
                    if (
                        not changed_provider
                        and base_url.strip().rstrip('/') != old_base_url
                        and not key
                    ):
                        return invalid('修改 OpenCode API 地址时，请重新输入 API Key。')
                    candidate['base_url'] = base_url
                else:
                    candidate.pop('base_url', None)

            try:
                save_config(candidate)
            except (OSError, TypeError, ValueError):
                app.logger.warning('config save failed')
                message = '配置保存失败。请检查配置文件权限后重试；API Key 需要重新输入。'
                if wants_json():
                    return jsonify(error=message), 503
                return render_config(values=values, form_error=message, status=503)
            # In-flight requests keep their own snapshot; new requests see
            # the complete saved configuration in one reference swap.
            cfg = candidate
            message = '设置已保存，之后的模型请求将使用新配置。'
            if wants_json():
                return jsonify(message=message, config=config_payload())
            flash(message)
            return redirect(url_for('config'), code=303)
        finally:
            busy.release()

    @app.post('/record')
    @mutation
    def record_save(api):
        q, thoughts = request.form.get('question', ''), request.form.get('user_thoughts', '')
        if not q.strip():
            abort(400)
        with uploaded_image_paths() as paths:
            error = api.record_error(
                q,
                thoughts,
                request.form.get('reference_answer'),
                origin='record',
                image_paths=paths,
            )
        return success(url_for('error_detail', error_id=error.id))

    @app.post('/api/record/draft')
    @mutation
    def record_draft(api):
        raw_input = request.form.get('raw_input', '')
        current_draft = {
            field: request.form.get(field, '')
            for field in ('question', 'user_thoughts', 'reference_answer')
        }
        with uploaded_image_paths() as paths:
            draft = api.prepare_record_draft(
                raw_input,
                paths,
                current_draft=current_draft,
            )
        return jsonify(
            draft=asdict(draft),
            **_record_draft_status(draft),
            submit_token=issue_token(request.path),
        )

    @app.get('/assistant-ui/bootstrap')
    def assistant_bootstrap():
        """Return the assistant slice's CSRF and operation-scoped tokens."""
        paths = {
            'record_draft': '/api/assistant/record/draft',
            'record_finalize': '/api/assistant/record/finalize',
            'record_reset': '/api/assistant/record/reset',
            'drill_new': '/api/assistant/drill/new',
            'grill_start': '/api/assistant/grill/start',
            'grill_answer': '/api/assistant/grill/answer',
            'teach_start': '/api/assistant/teach/start',
            'teach_answer': '/api/assistant/teach/answer',
            'teach_finish': '/api/assistant/teach/finish',
            'config_save': '/config',
        }
        with boundary() as api:
            list_errors = getattr(api, 'list_errors', lambda: [])
            payload = {
                'csrf': session['csrf'],
                'tokens': {name: issue_token(path) for name, path in paths.items()
                           if hasattr(api, 'list_errors') or name in {
                               'record_draft', 'record_finalize', 'record_reset', 'drill_new',
                               'grill_start', 'grill_answer'}},
                'history': _history_items(list_errors()),
                'configured': cfg.get('provider') == 'codex' or bool(cfg.get('api_key')) or llm is not None or application is not None,
                'record_pending_attachment_count': (
                    api.get_pending_attachment_count(session['record_pending_key'])
                    if isinstance(session.get('record_pending_key'), str)
                    and hasattr(api, 'get_pending_attachment_count') else 0
                ),
            }
            error_id = request.args.get('error_id')
            if error_id is not None:
                try:
                    numeric_error_id = int(error_id)
                    payload['workspace'] = public_workspace(api, numeric_error_id)
                    payload['tokens']['delete_error'] = issue_token(
                        f'/errors/{numeric_error_id}/delete'
                    )
                except (TypeError, ValueError):
                    abort(404)
        return jsonify(payload)

    @app.get('/api/assistant/workspace/<int:error_id>')
    def assistant_workspace(error_id):
        with boundary() as api:
            return jsonify(public_workspace(api, error_id))

    @app.get('/api/assistant/errors/<int:error_id>/attachments/<int:attachment_id>')
    def assistant_attachment(error_id, attachment_id):
        with boundary() as api:
            attachment = api.get_error_attachment(error_id, attachment_id)
        if attachment is None:
            raise ErrorNotFound()
        response = Response(attachment.data, mimetype=attachment.mime_type)
        response.headers['Content-Disposition'] = 'inline'
        return response

    @app.post('/api/assistant/record/draft')
    @mutation
    def assistant_record_draft(api):
        current_draft = {
            field: request_value(field, '')
            for field in ('question', 'user_thoughts', 'reference_answer')
        }
        with uploaded_image_paths() as paths:
            pending_key = record_pending_key()
            if paths:
                api.append_pending_image_attachments(pending_key, paths)
            draft = api.prepare_record_draft(
                request_value('raw_input', ''),
                paths,
                current_draft=current_draft,
                pending_key=pending_key,
            )
        pending_attachment_count = api.get_pending_attachment_count(pending_key)
        return jsonify(
            draft=asdict(draft),
            pending_attachment_count=pending_attachment_count,
            **_record_draft_status(draft, attachment_count=pending_attachment_count),
            submit_token=issue_token(request.path),
        )

    @app.post('/api/assistant/record/finalize')
    @mutation
    def assistant_record_finalize(api):
        with uploaded_image_paths() as paths:
            pending_key = session.get('record_pending_key')
            error = api.record_error(
                request_value('question', ''),
                request_value('user_thoughts', ''),
                request_value('reference_answer', '') or None,
                origin='record',
                image_paths=paths,
                pending_key=pending_key if isinstance(pending_key, str) else None,
            )
            attachment_count = api.get_error_attachment_count(error.id)
        session.pop('record_pending_key', None)
        return jsonify(
            error=public_error_payload(error),
            initial_attachment_count=attachment_count,
            submit_token=issue_token('/api/assistant/grill/start'),
            content_contract={
                'version': 1,
                'fields': {
                    'question': 'text',
                    'user_thoughts': 'text',
                    'reference_answer': 'text',
                },
                'persistence': 'legacy-text-plus-initial-original-attachments',
                'limitation': 'semantic field attachment refs are not persisted yet',
            },
        )

    @app.post('/api/assistant/record/reset')
    @mutation
    def assistant_record_reset(api):
        pending_key = session.pop('record_pending_key', None)
        if isinstance(pending_key, str) and pending_key:
            api.delete_pending_attachments(pending_key)
        return jsonify(submit_token=issue_token('/api/assistant/record/reset'))

    @app.post('/api/assistant/grill/start')
    @mutation
    def assistant_grill_start(api):
        result = api.start_or_resume_grill(request_int('error_id'))
        return jsonify(public_grill_payload(
            result, submit_token=issue_token('/api/assistant/grill/answer'),
        ))

    @app.post('/api/assistant/grill/answer')
    @mutation
    def assistant_grill_answer(api):
        error_id = request_int('error_id')
        answer = request_value('answer', '')
        with uploaded_image_paths() as paths:
            if not answer.strip() and not paths:
                abort(400)
            g.conversation_action = True
            result = api.submit_grill_answer(error_id, answer, image_paths=paths)
        return jsonify(public_grill_payload(
            result, submit_token=issue_token(request.path),
        ))

    @app.post('/api/assistant/teach/start')
    @mutation
    def assistant_teach_start(api):
        error_id = request_int('error_id')
        g.conversation_action = True
        result = api.start_or_resume_teach(error_id)
        return jsonify(
            workspace=public_workspace(api, error_id),
            assistant_response=result.assistant_response,
            submit_token=issue_token('/api/assistant/teach/answer'),
        )

    @app.post('/api/assistant/teach/answer')
    @mutation
    def assistant_teach_answer(api):
        error_id = request_int('error_id')
        answer = request_value('answer', '')
        with uploaded_image_paths() as paths:
            if not answer.strip() and not paths:
                abort(400)
            g.conversation_action = True
            result = api.submit_teach_answer(error_id, answer, image_paths=paths)
        return jsonify(
            workspace=public_workspace(api, error_id),
            assistant_response=result.assistant_response,
            submit_token=issue_token(request.path),
        )

    @app.post('/api/assistant/teach/finish')
    @mutation
    def assistant_teach_finish(api):
        error_id = request_int('error_id')
        api.finish_teach(error_id)
        key = create_drill_session(error_id=error_id)
        return jsonify(next_url=url_for('drill_detail', key=key))

    @app.get('/errors/<int:error_id>')
    def error_detail(error_id):
        with boundary() as api:
            if api.get_error(error_id) is None:
                raise ErrorNotFound()
        return assistant_shell()

    @app.get('/assistant-ui/<path:filename>')
    def assistant_ui_static(filename):
        # Keep API/bootstrap paths separate from the package-local asset path.
        return send_from_directory(assistant_dist, filename)

    @app.get('/legacy/errors/<int:error_id>')
    def legacy_error_detail(error_id):
        with boundary() as api:
            error = api.get_error(error_id)
            next_step = api.get_next_step(error_id) if error is not None else None
            initial_attachment_count = (
                api.get_error_attachment_count(error_id) if error is not None else 0
            )
        if error is None or next_step is None:
            raise ErrorNotFound()
        grill = messages(error.grilling_conversation, hide_grill_bootstrap=True)
        if grill and error.grilling_summary and grill[-1]['role'] == 'assistant' \
                and grill[-1]['content'] == error.grilling_summary:
            # Keep the persisted conversation untouched; the compact diagnosis
            # block below is the single Web presentation of this exact result.
            grill = grill[:-1]
        teach = messages(error.teach_conversation)
        grill_needs_answer = (
            error.status == 'pending-grill'
            and grill
            and grill[-1]['role'] == 'assistant'
        )
        teach_retry_needed = (
            error.status == 'pending-teach'
            and bool(teach)
            and teach[-1]['role'] == 'user'
        )
        step = None if grill_needs_answer else _next_action_view(
            next_step.code,
            error_id,
            teach_active=(
                error.status == 'pending-teach'
                and bool(teach)
                and not teach_retry_needed
            ),
        )
        question_length = len(error.question or '')
        thoughts_length = len(error.user_thoughts or '')
        reference_length = len(error.reference_answer or '')
        original_is_long = (
            max(question_length, thoughts_length, reference_length) > 520
            or question_length + thoughts_length + reference_length > 900
        )
        return render_template('detail.html', error=error,
                               grill=grill,
                               teach=teach,
                               error_title=_display_title(error),
                               question_excerpt=context_excerpt(error.question),
                               thoughts_excerpt=context_excerpt(error.user_thoughts),
                               reference_excerpt=context_excerpt(error.reference_answer),
                               original_is_long=original_is_long,
                               has_conversation=bool(grill or teach),
                               origin_label=_origin_view(error.origin),
                               initial_attachment_count=initial_attachment_count,
                               next_step=step,
                               current_error_id=error.id,
                               body_class='error-page')

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
            with uploaded_image_paths() as paths:
                if not answer.strip() and not paths:
                    abort(400)
                g.conversation_action = True
                if kind == 'grill':
                    operation(error_id, answer, image_paths=paths)
                else:
                    operation(error_id, answer, image_paths=paths)
            return success(url_for('error_detail', error_id=error_id, _anchor='composer'))
        g.conversation_action = action in {'start', 'answer'}
        operation(*args)
        return success(url_for('error_detail', error_id=error_id, _anchor='composer'))

    @app.post('/errors/<int:error_id>/teach/drill')
    @mutation
    def teach_to_drill(api, error_id):
        """Finish an active Teach explicitly before entering Drill."""
        api.finish_teach(error_id)
        key = create_drill_session(error_id=error_id)
        return success(url_for('drill_detail', key=key))

    @app.post('/errors/<int:error_id>/delete')
    @mutation
    def delete_error(api, error_id):
        # Require an explicit confirmation in addition to the CSRF and
        # one-time submission token checks performed by ``mutation``.
        if request.form.get('confirm_delete') != '1':
            abort(400)
        if api.get_error(error_id) is None:
            raise ErrorNotFound()
        api.delete_error(error_id)
        flash('Error 已删除。')
        return success(url_for('index'))

    def create_drill_session(*, error_id=None):
        key = secrets.token_urlsafe(24)
        with state_lock:
            drills[key] = dict(
                owner=session['sid'], stage='ready', preparation=None,
                judgment=None, answer='', error_id=error_id,
            )
            while len(drills) > 32:
                removable = next((k for k, v in drills.items() if v['stage'] not in {'spec', 'draft', 'judge'}), None)
                if removable is None:
                    abort(409)
                drills.pop(removable)
                with boundary() as api:
                    api.delete_pending_attachments(removable)
        return key

    def drill_target_views(api):
        return [
            {'id': error.id, 'title': _display_title(error)}
            for error in api.list_drill_targets()
        ]

    @app.get('/drill')
    def drill_new():
        return redirect(url_for('drill_detail', key=create_drill_session()), code=303)

    @app.get('/errors/<int:error_id>/drill')
    def drill_for_error(error_id):
        with boundary() as api:
            if api.get_error(error_id) is None:
                raise ErrorNotFound()
            if error_id not in {item.id for item in api.list_drill_targets()}:
                raise NoDrillContext()
        key = create_drill_session(error_id=error_id)
        return redirect(url_for('drill_detail', key=key), code=303)

    @app.post('/api/assistant/drill/new')
    @mutation
    def assistant_drill_new(api):
        raw_error_id = request_value('error_id', '')
        error_id = None
        if str(raw_error_id).strip():
            try:
                error_id = int(raw_error_id)
            except (TypeError, ValueError):
                abort(400)
            if error_id <= 0:
                abort(400)
            if api.get_error(error_id) is None:
                raise ErrorNotFound()
            if error_id not in {item.id for item in api.list_drill_targets()}:
                raise NoDrillContext()
        key = create_drill_session(error_id=error_id)
        return jsonify(next_url=url_for('drill_detail', key=key))

    def drill_payload(key, *, include_tokens=False):
        item = get_drill(key)
        prep, result = item['preparation'], item['judgment']
        with boundary() as api:
            pending_attachment_count = api.get_pending_attachment_count(key)
            target = api.get_error(item['error_id']) if item['error_id'] is not None else None
            target_options = drill_target_views(api)
        if item['error_id'] is not None and target is None:
            raise ErrorNotFound()
        public_result = None
        if result is not None:
            public_result = {
                'is_correct': result.is_correct,
                'derived_error_id': result.attempt.derived_error_id,
            }
        payload = {
            'key': key,
            'question': prep.question if prep else None,
            'result': public_result,
            'answer': item['answer'],
            'pending_attachment_count': pending_attachment_count,
            'state': _drill_state(item['stage']),
            'target_error': (
                None if target is None else {
                    'id': target.id,
                    'title': _display_title(target),
                }
            ),
            'target_options': target_options,
        }
        if include_tokens:
            payload.update(
                csrf=session['csrf'],
                tokens={
                    'prepare': issue_token(f'/api/drill/{key}/prepare'),
                    'judge': issue_token(f'/api/drill/{key}/judge'),
                },
            )
        return payload

    def render_drill_legacy(key):
        payload = drill_payload(key)
        return render_template('drill.html', **payload)

    @app.get('/drill/<key>')
    def drill_detail(key):
        return assistant_shell(legacy_html=render_drill_legacy(key))

    @app.get('/api/assistant/drill/<key>')
    def assistant_drill(key):
        return jsonify(drill_payload(key, include_tokens=True))

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
        raw_error_id = request_value('error_id', None)
        if raw_error_id is not None:
            if str(raw_error_id).strip():
                try:
                    selected_error_id = int(raw_error_id)
                except (TypeError, ValueError):
                    abort(400)
                if selected_error_id <= 0:
                    abort(400)
                if api.get_error(selected_error_id) is None:
                    raise ErrorNotFound()
                if selected_error_id not in {
                    target.id for target in api.list_drill_targets()
                }:
                    raise NoDrillContext()
                item['error_id'] = selected_error_id
            else:
                item['error_id'] = None
        def on_stage(stage):
            with state_lock:
                item['stage'] = stage.value
        try:
            item['preparation'] = api.prepare_drill(
                error_id=item['error_id'], on_stage=on_stage,
            )
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
        item['answer'], item['stage'] = answer, 'judge'
        g.judge_action = True
        with uploaded_image_paths() as paths:
            if not answer.strip() and not paths and api.get_pending_attachment_count(key) == 0:
                abort(400)
            try:
                item['judgment'] = api.judge_and_record_drill(
                    item['preparation'],
                    answer,
                    image_paths=paths,
                    pending_key=key,
                )
                item['stage'] = 'judged'
            except Exception:
                item['stage'] = 'prepared'
                raise
        return success(url_for('drill_detail', key=key))

    return app
