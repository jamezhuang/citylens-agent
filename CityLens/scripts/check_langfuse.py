"""Check configuration, or explicitly emit a synthetic trace; never read business data."""
import argparse
import asyncio
from contextlib import redirect_stderr, redirect_stdout
import io
import json
import os
from pathlib import Path
from types import SimpleNamespace
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from citylens import observability as obs

KEYS = {'CITYLENS_LANGFUSE_ENABLED', 'LANGFUSE_BASE_URL', 'LANGFUSE_PUBLIC_KEY', 'LANGFUSE_SECRET_KEY'}


def load_env(path):
    values = {}
    if not path.exists():
        return values
    for line in path.read_text(encoding='utf-8-sig').splitlines():
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        key, separator, value = line.partition('=')
        key = key.strip()
        if key not in KEYS:
            continue
        if not separator or key in values:
            raise ValueError('Duplicate or malformed Langfuse configuration')
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ('"', "'"):
            value = value[1:-1]
        values[key] = value
    return values


async def synthetic_probe():
    from citylens.llm import Completion
    from citylens.harness import AgentHarness
    class SyntheticModel:
        config = SimpleNamespace(provider='synthetic', model='connectivity-check-not-a-real-model')
        @obs.observed('model')
        async def complete(self):
            return Completion(usage={'input_tokens': 0, 'output_tokens': 0})
    async def read():
        return {'synthetic': True}
    @obs.observed('workflow')
    async def workflow():
        await SyntheticModel().complete()
        await AgentHarness({'connectivity_check': read}).call('connectivity_check')
        return SimpleNamespace(status='ok')
    # A distinctive root makes the synthetic check easy to find, no user IDs/content.
    with obs.Observation('citylens.connection_check', metadata={'synthetic': True}):
        trace_id = obs._client.get_current_trace_id()
        await workflow()
    await obs.flush()
    return trace_id


def verify_delivery(client, trace_id):
    expected = {'citylens.connection_check', 'citylens.workflow', 'citylens.model', 'citylens.tool'}
    rows = []
    for attempt in range(10):
        response = client.api.observations.get_many(trace_id=trace_id, limit=20)
        rows = response.data
        names = {row.name for row in rows}
        if expected <= names:
            return {'cloud_delivery_verified': True, 'observations': len(rows), 'names': sorted(names)}
        if attempt < 9:
            time.sleep(2)
    return {'cloud_delivery_verified': False, 'observations': len(rows),
            'names': sorted({row.name for row in rows})}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--env-file', type=Path, default=ROOT / '.env')
    parser.add_argument('--send', action='store_true', help='Authenticate and send a synthetic trace to Cloud')
    args = parser.parse_args()
    try:
        values = load_env(args.env_file)
        os.environ.update(values)
    except (OSError, ValueError) as exc:
        print(json.dumps({'state': 'invalid_configuration', 'error_type': type(exc).__name__}))
        return 2
    presence = {key: bool(os.getenv(key)) for key in sorted(KEYS)}
    if not args.send:
        print(json.dumps({'state': 'configuration_presence_only', 'fields_present': presence,
                          'network_requests_sent': 0, 'cloud_delivery_verified': False}))
        return 0 if all(presence.values()) else 2
    # Suppress vendor diagnostic bodies; output only safe summaries, never response text.
    with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
        obs.initialize()
    if obs._client is None:
        print(json.dumps(obs.status()))
        return 2
    try:
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            authenticated = obs._client.auth_check()
            trace_id = asyncio.run(synthetic_probe()) if authenticated else None
            delivery = verify_delivery(obs._client, trace_id) if trace_id else {'cloud_delivery_verified': False}
        print(json.dumps({'authenticated': authenticated, 'trace_id': trace_id,
                          'synthetic_trace_created': trace_id is not None,
                          **delivery,
                          'next_check': 'Find citylens.connection_check and this trace_id in your Langfuse project.'}))
        return 0 if authenticated and delivery['cloud_delivery_verified'] else 1
    except Exception as exc:
        print(json.dumps({'state': 'connection_failed', 'error_type': type(exc).__name__,
                          'cloud_delivery_verified': False}))
        return 1
    finally:
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            try:
                obs._client.shutdown()
            except Exception:
                pass


if __name__ == '__main__':
    raise SystemExit(main())
