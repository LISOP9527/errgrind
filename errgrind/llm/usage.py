"""Content-free model usage telemetry; never part of workflow persistence."""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timedelta, timezone
from functools import wraps
import json
import os
from pathlib import Path
import re
import threading
import time
import uuid
import warnings

_CONTEXT = ContextVar('errgrind_usage_context', default=None)
_ATTEMPT = ContextVar('errgrind_usage_attempt', default=None)
_LOCK = threading.Lock()
_WARNED = False
MAX_LOG_BYTES = 5 * 1024 * 1024
BACKUP_COUNT = 3
TOKEN_FIELDS = ('input_tokens', 'cached_input_tokens', 'output_tokens', 'reasoning_tokens', 'total_tokens')


def log_path() -> Path:
    root = os.environ.get('XDG_STATE_HOME') or str(Path.home() / '.local' / 'state')
    return Path(root) / 'errgrind' / 'usage.jsonl'


def _number(value):
    return value if type(value) is int and value >= 0 else None


def _label(value):
    # Only identifiers enter the log, never arbitrary objects or error messages.
    return value if isinstance(value, str) and re.fullmatch(r'[a-zA-Z0-9_.:/-]{1,120}', value) else None


def normalize_usage(raw, format: str) -> dict:
    result = dict.fromkeys(TOKEN_FIELDS)
    if not isinstance(raw, dict):
        return result
    def nested(key, field):
        data = raw.get(key)
        return data.get(field) if isinstance(data, dict) else None
    if format == 'codex':
        values = (raw.get('input_tokens'), nested('input_tokens_details', 'cached_tokens'),
                  raw.get('output_tokens'), nested('output_tokens_details', 'reasoning_tokens'), raw.get('total_tokens'))
    elif format == 'openai':
        cached = nested('prompt_tokens_details', 'cached_tokens')
        if cached is None:
            cached = raw.get('prompt_cache_hit_tokens')  # DeepSeek's native field.
        values = (raw.get('prompt_tokens'), cached, raw.get('completion_tokens'),
                  nested('completion_tokens_details', 'reasoning_tokens'), raw.get('total_tokens'))
    elif format == 'gemini':
        # Gemini exposes candidate output and thoughts separately. Keep its
        # reported values; do not fabricate totals or add thoughts twice.
        values = (raw.get('promptTokenCount'), raw.get('cachedContentTokenCount'),
                  raw.get('candidatesTokenCount'), raw.get('thoughtsTokenCount'), raw.get('totalTokenCount'))
    else:
        return result
    return dict(zip(TOKEN_FIELDS, map(_number, values)))


@contextmanager
def usage_scope(**fields):
    current = _CONTEXT.get()
    context = dict(current or {'operation_id': uuid.uuid4().hex, '_counter': [0]})
    # Context only accepts internally chosen identifiers and integer counters.
    for key in ('action', 'stage'):
        if key in fields:
            context[key] = _label(fields[key])
    for key in ('error_id', 'repair_attempt', 'json_attempt'):
        if key in fields:
            context[key] = _number(fields[key])
    token = _CONTEXT.set(context)
    try:
        yield
    finally:
        _CONTEXT.reset(token)


def usage_action(action: str, stage: str):
    """Tag a single application invocation, including all repairs within it."""
    def decorate(function):
        @wraps(function)
        def wrapped(*args, **kwargs):
            error_id = kwargs.get('error_id')
            if error_id is None and len(args) > 1 and type(args[1]) is int:
                error_id = args[1]
            with usage_scope(action=action, stage=stage, error_id=error_id):
                return function(*args, **kwargs)
        return wrapped
    return decorate


class _ModelAttempt:
    def __init__(self, provider, model, reasoning_effort, sdk_max_retries):
        context = _CONTEXT.get() or {'operation_id': uuid.uuid4().hex, '_counter': [0]}
        context['_counter'][0] += 1
        self.row = {
            'version': 1, 'event': 'model_attempt',
            'timestamp': datetime.now(timezone.utc).isoformat(),
            'operation_id': context['operation_id'], 'attempt_id': uuid.uuid4().hex,
            'attempt_index': context['_counter'][0],
            'action': context.get('action') or 'unscoped',
            'stage': context.get('stage') or 'generation',
            'error_id': context.get('error_id'),
            'repair_attempt': context.get('repair_attempt', 1),
            'json_attempt': context.get('json_attempt', 1),
            'provider': _label(provider), 'model': _label(model),
            'reasoning_effort': _label(reasoning_effort),
            'sdk_max_retries': _number(sdk_max_retries),
            'http_status': None, 'usage_reported': False,
            **dict.fromkeys(TOKEN_FIELDS),
        }

    def record_usage(self, raw, format: str):
        try:
            parsed = normalize_usage(raw, format)
            # Stream metadata is a cumulative snapshot, not an incremental bill.
            for key, value in parsed.items():
                if value is not None:
                    self.row[key] = value
                    self.row['usage_reported'] = True
        except Exception:
            pass

    def set_http_status(self, status):
        value = _number(status)
        if value is not None and 100 <= value <= 599:
            self.row['http_status'] = value


@contextmanager
def model_attempt(provider: str, model: str, reasoning_effort=None, sdk_max_retries=None):
    attempt = _ModelAttempt(provider, model, reasoning_effort, sdk_max_retries)
    token = _ATTEMPT.set(attempt)
    started = time.monotonic()
    status, error_type = 'success', None
    try:
        yield attempt
    except BaseException as exc:
        attempt.set_http_status(getattr(exc, 'status_code', None))
        response = getattr(exc, 'response', None)
        if response is not None:
            attempt.set_http_status(getattr(response, 'status_code', None))
        status = 'interrupted' if isinstance(exc, (KeyboardInterrupt, EOFError, GeneratorExit)) else 'error'
        error_type = _label(type(exc).__name__)
        raise
    finally:
        _ATTEMPT.reset(token)
        attempt.row.update(status=status, error_type=error_type,
                           duration_ms=round((time.monotonic() - started) * 1000, 2))
        _write_event(attempt.row)


def record_usage(raw, format: str):
    active = _ATTEMPT.get()
    if active is not None:
        active.record_usage(raw, format)


def record_http_status(status):
    active = _ATTEMPT.get()
    if active is not None:
        active.set_http_status(status)


def _write_event(row):
    global _WARNED
    try:
        path = log_path()
        line = (json.dumps(row, ensure_ascii=False, separators=(',', ':')) + '\n').encode('utf-8')
        with _LOCK:
            path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            # Lock rotation as well as append across concurrent CLI processes.
            import fcntl
            lock_fd = os.open(str(path) + '.lock', os.O_CREAT | os.O_WRONLY, 0o600)
            try:
                fcntl.flock(lock_fd, fcntl.LOCK_EX)
                if path.exists() and path.stat().st_size + len(line) > MAX_LOG_BYTES:
                    for index in range(BACKUP_COUNT, 0, -1):
                        source = path if index == 1 else Path(f'{path}.{index - 1}')
                        if source.exists():
                            source.replace(Path(f'{path}.{index}'))
                fd = os.open(path, os.O_CREAT | os.O_WRONLY | os.O_APPEND, 0o600)
                with os.fdopen(fd, 'ab') as output:
                    os.fchmod(output.fileno(), 0o600)
                    output.write(line)
            finally:
                os.close(lock_fd)
    except Exception:
        # Disk errors must not roll back an accepted answer or mask Ctrl+C.
        if not _WARNED:
            _WARNED = True
            try:
                warnings.warn('无法写入模型用量日志；本次学习流程继续，用量记录可能缺失。', RuntimeWarning)
            except Exception:
                pass


def summarize(path: Path, days: int | None = None) -> dict:
    """Aggregate only reported values; missing usage never becomes zero usage."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=days) if days is not None else None
    groups = {}
    malformed = 0
    for file in [Path(f'{path}.{n}') for n in range(BACKUP_COUNT, 0, -1)] + [path]:
        if not file.exists():
            continue
        with file.open(encoding='utf-8') as stream:
            for line in stream:
                try:
                    row = json.loads(line)
                    if row.get('event') != 'model_attempt':
                        continue
                    stamp = datetime.fromisoformat(row['timestamp'])
                    if cutoff is not None and stamp < cutoff:
                        continue
                    key = tuple(row.get(k) for k in ('action', 'stage', 'provider', 'model', 'reasoning_effort'))
                    group = groups.setdefault(key, {
                        **dict(zip(('action', 'stage', 'provider', 'model', 'reasoning_effort'), key)),
                        'attempts': 0, 'errors': 0, 'interrupted': 0, 'missing_usage_attempts': 0,
                        'repair_attempt_requests': 0, 'json_retry_requests': 0,
                        'duration_ms': 0, 'reported_tokens': dict.fromkeys(TOKEN_FIELDS, 0),
                        'reporting_attempts': dict.fromkeys(TOKEN_FIELDS, 0),
                    })
                    group['attempts'] += 1
                    group['errors'] += row.get('status') == 'error'
                    group['interrupted'] += row.get('status') == 'interrupted'
                    group['missing_usage_attempts'] += not row.get('usage_reported', False)
                    group['repair_attempt_requests'] += (row.get('repair_attempt') or 1) > 1
                    group['json_retry_requests'] += (row.get('json_attempt') or 1) > 1
                    group['duration_ms'] += row.get('duration_ms', 0)
                    for field in TOKEN_FIELDS:
                        value = _number(row.get(field))
                        if value is not None:
                            group['reported_tokens'][field] += value
                            group['reporting_attempts'][field] += 1
                except (ValueError, TypeError, KeyError, AttributeError):
                    malformed += 1
    result = list(groups.values())
    for group in result:
        for field in TOKEN_FIELDS:
            if group['reporting_attempts'][field] == 0:
                group['reported_tokens'][field] = None
        group['duration_ms'] = round(group['duration_ms'], 2)
    return {'log_path': str(path), 'days': days, 'malformed_lines': malformed, 'groups': result}
