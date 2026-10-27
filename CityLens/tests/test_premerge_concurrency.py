"""Concurrent boundaries, not sequential duplicate confirmation checks."""
import asyncio

import httpx
import pytest

from citylens.actions import ActionError, VoucherActionWorkflow
from citylens.dianping import DianpingClient
from citylens.runs import RunStore
from citylens.schemas import ChatResponse, ShopCard
from citylens.storage import StateDatabase


@pytest.mark.parametrize('separate_workers', [False, True])
def test_concurrent_confirmation_has_one_external_write(tmp_path, separate_workers):
    async def run():
        started, release = asyncio.Event(), asyncio.Event()
        writes = []
        async def backend(request):
            if request.url.path == '/shop/1':
                data = {'id': 1, 'name': 'test'}
            elif request.url.path == '/voucher/list/1':
                data = [{'id': 9, 'shopId': 1, 'title': 'test', 'payValue': 0, 'actualValue': 500, 'type': 0}]
            elif request.url.path == '/voucher-order/ordinary/9':
                writes.append(9)
                started.set()
                await release.wait()
                data = 88
            elif request.url.path == '/voucher-order/88':
                data = {'id': 88, 'voucherId': 9, 'status': 2}
            else:
                return httpx.Response(404)
            return httpx.Response(200, json={'success': True, 'data': data})
        client = DianpingClient('http://test', transport=httpx.MockTransport(backend))
        database = StateDatabase(str(tmp_path / 'state.sqlite3'))
        runs = RunStore(database=database)
        await runs.save(ChatResponse(answer='test', status='ok', mode='rules', run_id='race',
            shops=[ShopCard(id=1, name='test', url='http://test/1', reason='test')]), 7)
        actions = VoucherActionWorkflow(client, runs)
        other = VoucherActionWorkflow(client, RunStore(database=database)) if separate_workers else actions
        draft = await actions.prepare(7, 'race', 1, 9)
        first = asyncio.create_task(actions.confirm(draft['action_id'], 7, 'owner'))
        await asyncio.wait_for(started.wait(), 2)
        second = asyncio.create_task(other.confirm(draft['action_id'], 7, 'owner'))
        try:
            if separate_workers:
                with pytest.raises(ActionError):
                    await asyncio.wait_for(second, 2)
            release.set()
            result = await asyncio.wait_for(first, 2)
            assert result['order_id'] == '88'
            if not separate_workers:
                assert (await asyncio.wait_for(second, 2))['order_id'] == '88'
            assert (await other.status(draft['action_id'], 7, 'owner'))['order_id'] == '88'
            assert writes == [9]
        finally:
            release.set()
            await asyncio.gather(first, second, return_exceptions=True)
            await client.close()
    asyncio.run(run())
