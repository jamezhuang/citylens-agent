import asyncio
import httpx
from evaluation.catalog import load_catalog
from evaluation.execution import RunBudget, execute_case
from evaluation.mock_backend import MockBusiness


def test_business_transport_never_falls_back_to_network():
    async def run():
        _, _, fixtures = load_catalog()
        backend = MockBusiness(fixtures, {'fixture': 'base'})
        async with httpx.AsyncClient(transport=httpx.MockTransport(backend)) as client:
            response = await client.get('https://synthetic.invalid/not-implemented')
            assert response.status_code == 501
        assert backend.calls[0]['operation'] == 'unsupported_route'
    asyncio.run(run())


def test_rules_adapter_runs_real_workflow_with_separate_state():
    async def run():
        suite, cases, fixtures = load_catalog()
        case = next(case for case in cases if case['id'] == 'S01')
        budget = RunBudget()
        record = await execute_case(case, suite, fixtures, 'mock_rules', budget)
        assert record['execution_status'] == 'executed'
        step = record['steps'][0]
        assert step['response']['body']['mode'] == 'rules'
        assert step['evidence']['calls_complete']
        assert any(call['operation'] == 'search_shops' for call in step['calls'])
        assert not record['mock_orders_created']
        assert budget.calls == 0
    asyncio.run(run())


def test_deadline_injection_follows_actual_verification_and_stops_next_call():
    async def run():
        suite, cases, fixtures = load_catalog()
        case = next(case for case in cases if case['id'] == 'F07')
        record = await execute_case(case, suite, fixtures, 'mock_rules', RunBudget())
        assert record['execution_status'] == 'executed'
        assert record['phase_injections'][0]['shop_id'] == 101
        step = record['steps'][0]
        assert step['response']['body']['status'] == 'partial'
        assert [s['id'] for s in step['response']['body']['shops']] == [101]
        assert len([c for c in step['calls'] if c['operation'] == 'get_shop']) == 1
        assert step['metrics']['calls_started_after_deadline'] == 0
        assert '部分候选' in step['response']['body']['answer']
    asyncio.run(run())
