import asyncio
from contextlib import contextmanager
import json
from types import SimpleNamespace

import pytest

from citylens import observability as obs
from citylens.harness import AgentHarness
from citylens.llm import Completion


@pytest.fixture
def collector(monkeypatch):
    events = []
    class Span:
        def update(self, **values):
            events.append(('update', values))
    class Client:
        @contextmanager
        def start_as_current_observation(self, **values):
            events.append(('start', values))
            try:
                yield Span()
            finally:
                events.append(('end', {}))
    monkeypatch.setattr(obs, '_client', Client())
    return events


def test_nested_workflow_model_tool_no_contents_or_credentials(collector):
    class Provider:
        config = SimpleNamespace(model='test-model', provider='deepseek')
        @obs.observed('model')
        async def complete(self, prompt, **kwargs):
            return Completion(text='private model response', usage={'input_tokens': 12, 'output_tokens': 3})

    async def tool(phone):
        return {'secret': 'private tool result'}

    @obs.observed('workflow')
    async def run(token):
        await Provider().complete('private prompt', authorization=token)
        await AgentHarness({'read': tool}).call('read', phone='13812345678')
        return SimpleNamespace(status='ok')

    asyncio.run(run('Bearer secret-token'))
    starts = [e[1] for e in collector if e[0] == 'start']
    assert [e['name'] for e in starts] == ['citylens.workflow', 'citylens.model', 'citylens.tool']
    assert starts[1]['as_type'] == 'generation' and starts[2]['metadata']['tool'] == 'read'
    assert any(e[1].get('usage_details') == {'input': 12, 'output': 3} for e in collector)
    serialized = json.dumps(collector)
    for value in ['private prompt', 'private model response', 'private tool result', '13812345678', 'secret-token']:
        assert value not in serialized


@pytest.mark.parametrize('error', [TimeoutError('secret failure'), asyncio.CancelledError('secret cancelled')])
def test_error_and_cancellation_are_recorded_without_exception_message(collector, error):
    @obs.observed('workflow')
    async def run():
        raise error
    with pytest.raises(type(error)):
        asyncio.run(run())
    text = json.dumps(collector)
    assert 'secret' not in text
    assert type(error).__name__ in text
    assert any(e[1].get('level') == 'ERROR' for e in collector)


def test_exporter_failures_never_repeat_or_change_business_operation(monkeypatch):
    class Broken:
        def start_as_current_observation(self, **kwargs):
            raise RuntimeError('private exporter error')
    monkeypatch.setattr(obs, '_client', Broken())
    calls = []
    @obs.observed('workflow')
    async def run():
        calls.append(1)
        return 42
    assert asyncio.run(run()) == 42 and calls == [1]
    assert obs.status()['last_instrumentation_error'] == 'RuntimeError'


@pytest.mark.parametrize('endpoint', ['https://cloud.langfuse.com:bad', 'https://user:secret@cloud.langfuse.com',
                                    'https://attacker.invalid', 'http://cloud.langfuse.com', 'https://[broken'])
def test_invalid_configuration_is_safe_and_never_initializes_sdk(monkeypatch, endpoint):
    monkeypatch.setattr(obs, '_client', None)
    monkeypatch.setenv('CITYLENS_LANGFUSE_ENABLED', 'true')
    monkeypatch.setenv('LANGFUSE_PUBLIC_KEY', 'public')
    monkeypatch.setenv('LANGFUSE_SECRET_KEY', 'secret')
    monkeypatch.setenv('LANGFUSE_BASE_URL', endpoint)
    obs.initialize()
    assert obs.status()['state'] == 'configuration_required'
    assert not obs.status()['enabled']


def test_actual_sdk_exports_one_trace_with_parent_links_without_network(monkeypatch):
    from langfuse import Langfuse
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    client = Langfuse(public_key='pk-lf-citylens-offline-test', secret_key='not-a-real-key',
                      base_url='https://cloud.langfuse.com', tracer_provider=provider, span_exporter=exporter)
    monkeypatch.setattr(obs, '_client', client)
    class Model:
        config = SimpleNamespace(model='test-model', provider='synthetic')
        @obs.observed('model')
        async def complete(self):
            return Completion(text='private response', usage={'input_tokens': 11, 'output_tokens': 2})
    async def read():
        return {'secret': 'private tool result'}
    @obs.observed('workflow')
    async def run():
        with obs.Observation('citylens.child'):
            pass
        await Model().complete()
        await AgentHarness({'read': read}).call('read')
    try:
        asyncio.run(run())
        client.flush()
        spans = exporter.get_finished_spans()
        parent = next(s for s in spans if s.name == 'citylens.workflow')
        child = next(s for s in spans if s.name == 'citylens.child')
        assert parent.context.trace_id == child.context.trace_id
        assert child.parent.span_id == parent.context.span_id
        assert {'citylens.workflow', 'citylens.model', 'citylens.tool', 'citylens.child'} <= {s.name for s in spans}
        for span in spans:
            assert span.context.trace_id == parent.context.trace_id
            if span.name != 'citylens.workflow':
                assert span.parent.span_id == parent.context.span_id
            text = json.dumps(dict(span.attributes))
            assert 'private response' not in text and 'private tool result' not in text
    finally:
        client.shutdown()


def test_run_id_correlates_with_local_record_without_session_or_user_ids(collector):
    run_id = '167a373e-0350-4b9a-88aa-61372c06fb30'
    @obs.observed('workflow')
    async def run():
        return SimpleNamespace(status='ok', run_id=run_id, session_id='private-session', user_id=99)
    asyncio.run(run())
    assert any(e[1].get('metadata', {}).get('run_id') == run_id for e in collector)
    assert 'private-session' not in json.dumps(collector)
