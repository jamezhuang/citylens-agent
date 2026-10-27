import asyncio

import httpx
import pytest

from citylens.dianping import DianpingClient
from citylens.harness import AgentHarness
from citylens.planner import Planner
from citylens.run_context import BudgetExceeded, RunContext
from citylens.schemas import ChatRequest
from citylens.workflow import DiscoveryWorkflow


def test_estimated_call_larger_than_remaining_time_never_starts():
    async def run():
        calls = []
        async def read():
            calls.append(1)
        context = RunContext(max_duration=2, tool_time_estimates={'read': 3})
        harness = AgentHarness({'read': read}, context=context)
        with pytest.raises(BudgetExceeded) as error:
            await harness.call('read')
        assert error.value.code == 'insufficient_time'
        assert context.calls == 0 and not calls
    asyncio.run(run())


def test_read_timeout_preserves_only_previously_verified_shops(tmp_path):
    async def run():
        calls = []
        shops = [{'id': i, 'name': f'火锅{i}', 'typeId': 1, 'avgPrice': 80, 'score': 48} for i in (1, 2, 3)]
        def backend(request):
            path = request.url.path
            if path == '/shop-type/list':
                data = [{'id': 1, 'name': '美食'}]
            elif path == '/shop/search':
                return httpx.Response(200, json={'success': True, 'data': shops, 'total': 3})
            else:
                calls.append(path)
                if path == '/shop/2':
                    raise httpx.ReadTimeout('test timeout', request=request)
                data = shops[int(path.rsplit('/', 1)[-1]) - 1]
            return httpx.Response(200, json={'success': True, 'data': data})
        client = DianpingClient('http://test', transport=httpx.MockTransport(backend))
        try:
            workflow = DiscoveryWorkflow(client, Planner(), 'http://test', str(tmp_path / 'state.sqlite3'))
            result = await workflow.run(ChatRequest(message='找人均150以内的火锅'), 7)
            assert result.status == 'partial' and [s.id for s in result.shops] == [1]
            assert calls == ['/shop/1', '/shop/2']
            assert '超时' in result.answer and '其余候选' in result.answer
            assert workflow.checkpoints.get(result.run_id, 7)['phase'] == 'retryable'
            state = await workflow.sessions.load(result.session_id, 7)
            assert state.last_shop_ids == [1]
        finally:
            await client.close()
    asyncio.run(run())
