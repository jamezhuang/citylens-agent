import asyncio

import httpx
import pytest

from citylens.dianping import DianpingClient
from citylens.planner import Planner, rule_plan
from citylens.schemas import ChatRequest, SearchPlan
from citylens.session import merge_followup
from citylens.workflow import DiscoveryWorkflow
from evaluation.catalog import load_catalog
from evaluation.execution import RunBudget, execute_case


def test_positional_model_category_does_not_drop_itinerary():
    current = rule_plan('两人先吃火锅再唱歌，总预算500元')
    update = SearchPlan(category='KTV', keyword=None, followup=True, relative_price='lower')
    merged = merge_followup('第二家换便宜一点，第一家不变', current, update, [101, 203], [80, 120])
    assert [(s.category, s.keyword) for s in merged.stops] == [('美食', '火锅'), ('KTV', None)]
    assert merged.fixed_first_shop_id == 101 and merged.second_price_ceiling == 120
    assert merged.total_budget == 500 and merged.relative_price is None


@pytest.mark.parametrize('case_id', ['S06', 'F04'])
def test_reserved_faults_have_deterministic_verification(case_id):
    async def run():
        suite, cases, fixtures = load_catalog()
        record = await execute_case(next(c for c in cases if c['id'] == case_id), suite, fixtures,
                                    'mock_rules', RunBudget())
        body = record['steps'][0]['response']['body']
        if case_id == 'S06':
            assert body['shops'] and all(s['id'] != 101 for s in body['shops'])
            assert any(e['event'] == 'candidate_detail_changed' and e['shop_id'] == 101 for e in body['events'])
        else:
            assert body['status'] == 'upstream_error' and not body['shops']
            assert '数据与筛选条件不符' in body['answer'] and '放宽' not in body['answer']
    asyncio.run(run())


def test_full_search_summary_does_not_substitute_for_failed_detail():
    async def run():
        def backend(request):
            if request.url.path == '/shop-type/list':
                return httpx.Response(200, json={'success': True, 'data': [{'id': 1, 'name': '美食'}]})
            if request.url.path == '/shop/search':
                return httpx.Response(200, json={'success': True, 'data': [
                    {'id': 1, 'name': '火锅', 'typeId': 1, 'avgPrice': 80, 'score': 48}], 'total': 1})
            return httpx.Response(503)
        client = DianpingClient('http://test', transport=httpx.MockTransport(backend))
        try:
            result = await DiscoveryWorkflow(client, Planner(), 'http://test').run(ChatRequest(message='找人均100以内的火锅'))
            assert result.status == 'upstream_error' and not result.shops
            assert '详情未能可靠核验' in result.answer
        finally:
            await client.close()
    asyncio.run(run())
