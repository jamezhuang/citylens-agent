"""Opt-in Langfuse tracing. Never pass business text, arguments or exceptions to SDK."""
from __future__ import annotations

import asyncio
import hashlib
from contextvars import ContextVar
from functools import wraps
import os
import re
import time
from pathlib import Path
from urllib.parse import urlsplit
from uuid import UUID

_client = None
_state = 'disabled'
_active = ContextVar('citylens_observation', default=False)
_last_error = None
_release = None
_STATUSES = {'ok', 'partial', 'needs_location', 'needs_clarification', 'no_results',
             'upstream_error', 'business_rejected', 'session_conflict', 'unsupported_request'}


def _safe_id(value):
    return value if isinstance(value, str) and re.fullmatch(r'[A-Za-z0-9_.:/-]{1,100}', value) and 'sk-' not in value else 'unspecified'


def initialize():
    """Explicit opt-in; absent or invalid configuration does not prevent serving."""
    global _client, _state, _last_error, _release
    if _client is not None:
        return
    if os.getenv('CITYLENS_LANGFUSE_ENABLED', '').lower() != 'true':
        _state = 'disabled'
        return
    public, secret = os.getenv('LANGFUSE_PUBLIC_KEY', ''), os.getenv('LANGFUSE_SECRET_KEY', '')
    endpoint = os.getenv('LANGFUSE_BASE_URL', '').rstrip('/')
    try:
        url = urlsplit(endpoint)
        port = url.port
    except ValueError:
        _state = 'configuration_required'
        return
    # No default region: keys must be sent only to the explicitly selected host.
    allowed = {'cloud.langfuse.com', 'us.cloud.langfuse.com', 'jp.cloud.langfuse.com', 'hipaa.cloud.langfuse.com'}
    if (not public or not secret or url.scheme != 'https' or url.hostname not in allowed
            or url.username or url.password or url.query or url.fragment or url.path or port not in (None, 443)):
        _state = 'configuration_required'
        return
    try:
        from langfuse import Langfuse
        digest = hashlib.sha256()
        for source in sorted(Path(__file__).parent.glob('*.py')):
            digest.update(source.name.encode())
            digest.update(source.read_bytes())
        _release = digest.hexdigest()
        _client = Langfuse(public_key=public, secret_key=secret, base_url=endpoint,
                           timeout=2, environment='stable', debug=False, release=_release,
                           should_export_span=lambda span: span.name.startswith('citylens.'))
        _state = 'configured'
    except Exception as exc:
        _last_error = type(exc).__name__
        _state = 'initialization_failed'


def status():
    return {'state': _state, 'enabled': _client is not None, 'content_capture': False,
            'cloud_delivery_verified': None, 'release': _release,
            'last_instrumentation_error': _last_error}


class Observation:
    def __init__(self, name, *, kind='span', metadata=None, model=None):
        self.name, self.kind, self.metadata, self.model = name, kind, metadata or {}, model
        self.manager = self.span = self.token = None

    def __enter__(self):
        global _last_error
        if _client is None:
            return self
        try:
            options = {'name': self.name, 'as_type': self.kind, 'metadata': self.metadata}
            if self.model:
                options['model'] = self.model
            self.manager = _client.start_as_current_observation(**options)
            self.span = self.manager.__enter__()
            self.token = _active.set(True)
        except Exception as exc:
            _last_error = type(exc).__name__
        return self

    def update(self, **safe_fields):
        global _last_error
        if self.span is not None:
            try:
                self.span.update(**safe_fields)
            except Exception as exc:
                _last_error = type(exc).__name__

    def __exit__(self, exc_type, exc, tb):
        global _last_error
        try:
            if self.manager is not None and self.span is not None:
                # Do not let the SDK capture exception messages/stack locals.
                self.manager.__exit__(None, None, None)
        except Exception as error:
            _last_error = type(error).__name__
        finally:
            if self.token is not None:
                _active.reset(self.token)
        return False


def observed(stage):
    """Capture only code-owned labels, durations, status and numeric usage."""
    def decorate(function):
        @wraps(function)
        async def wrapper(*args, **kwargs):
            metadata = {'stage': stage}
            model = None
            if stage == 'model' and args:
                config = getattr(args[0], 'config', None)
                model = _safe_id(getattr(config, 'model', ''))
                metadata['provider'] = _safe_id(getattr(config, 'provider', ''))
            if stage == 'tool' and len(args) > 1:
                name = args[1]
                metadata['tool'] = _safe_id(name) if name in args[0].specs else 'unregistered'
            started = time.monotonic()
            with Observation('citylens.' + stage, kind='generation' if stage == 'model' else 'span',
                             metadata=metadata, model=model) as observation:
                try:
                    result = await function(*args, **kwargs)
                    usage = getattr(result, 'usage', {})
                    usage_details = {label: usage[field] for label, field in [('input', 'input_tokens'), ('output', 'output_tokens')]
                                     if type(usage.get(field)) is int and usage[field] >= 0}
                    fields = {'metadata': {**metadata, 'outcome': 'success',
                              'elapsed_ms': round((time.monotonic() - started) * 1000)}}
                    result_status = getattr(result, 'status', None)
                    if result_status in _STATUSES:
                        fields['metadata']['business_status'] = result_status
                    run_id = getattr(result, 'run_id', None)
                    if isinstance(run_id, str):
                        try:
                            fields['metadata']['run_id'] = str(UUID(run_id))
                        except ValueError:
                            pass
                    if stage == 'model' and usage_details:
                        fields['usage_details'] = usage_details
                    observation.update(**fields)
                    return result
                except BaseException as exc:
                    observation.update(level='ERROR', metadata={**metadata,
                        'outcome': 'cancelled' if isinstance(exc, asyncio.CancelledError) else 'error',
                        'error_type': _safe_id(type(exc).__name__),
                        'elapsed_ms': round((time.monotonic() - started) * 1000)})
                    raise
        return wrapper
    return decorate


async def flush():
    global _last_error
    if _client is not None:
        try:
            await asyncio.wait_for(asyncio.to_thread(_client.flush), timeout=3)
        except Exception as exc:
            _last_error = type(exc).__name__
