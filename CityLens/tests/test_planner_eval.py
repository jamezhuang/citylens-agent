import asyncio
import json
import httpx
import pytest

from citylens.llm import LLMConfig, LLMProvider
from citylens.harness import AgentHarness
from citylens.planner import Planner
from scripts.eval_planner import evaluate


def test_invalid_model_plan_logs_field_and_type_without_values():
    def model(_request):
        return httpx.Response(200, json={'content': [{'type': 'text', 'text': json.dumps(
            {'category': '美食', 'followup': 'private-invalid-value', 'sort': 'score'})}]})

    async def run():
        provider = LLMProvider(LLMConfig(provider='anthropic', api_key='test',
                                       base_url='https://example.test', model='test'), httpx.MockTransport(model))
        try:
            harness = AgentHarness({})
            plan, mode = await Planner(provider=provider).plan('找人均150以内的火锅', harness=harness)
            assert mode == 'rules' and plan.max_price == 150
            event = next(e for e in harness.events if e['event'] == 'planner_fallback')
            assert event['validation_errors'] == [{'field': 'followup', 'type': 'bool_parsing'}]
            assert 'private-invalid-value' not in json.dumps(harness.events)
        finally:
            await provider.close()
    asyncio.run(run())


@pytest.mark.parametrize('nearby,message,expected', [
    (None, '找附近的火锅', True),
    (None, '找火锅', False),
    ([], '找火锅', None),
])
def test_nullable_intent_booleans_use_user_text_but_invalid_types_fail(nearby, message, expected):
    def model(_request):
        return httpx.Response(200, json={'content': [{'type': 'text', 'text': json.dumps(
            {'category': '美食', 'nearby': nearby, 'wants_coupon': None, 'wants_notes': None})}]})

    async def run():
        provider = LLMProvider(LLMConfig(provider='anthropic', api_key='test',
                                       base_url='https://example.test', model='test'), httpx.MockTransport(model))
        try:
            harness = AgentHarness({})
            plan, mode = await Planner(provider=provider).plan(message, harness=harness)
            if expected is None:
                assert mode == 'rules'
                event = next(e for e in harness.events if e['event'] == 'planner_fallback')
                assert event['validation_errors'] == [{'field': 'nearby', 'type': 'bool_type'}]
            else:
                assert mode == 'model' and plan.nearby is expected
                event = next(e for e in harness.events if e['event'] == 'planner_model')
                assert set(event['normalized_fields']) == {'nearby', 'wants_coupon', 'wants_notes'}
                assert plan.wants_coupon is False and plan.wants_notes is False
        finally:
            await provider.close()
    asyncio.run(run())


@pytest.mark.parametrize('keyword,expected', [('KTV', None), ('唱歌', None), ('欢乐KTV', '欢乐KTV'), ('火锅', None)])
def test_category_switch_does_not_create_name_filter(keyword, expected):
    def model(_request):
        return httpx.Response(200, json={'content': [{'type': 'text', 'text': json.dumps(
            {'category': 'KTV', 'keyword': keyword, 'followup': True, 'sort': 'score'})}]})
    async def run():
        provider = LLMProvider(LLMConfig(provider='anthropic', api_key='test',
                                       base_url='https://example.test', model='test'), httpx.MockTransport(model))
        try:
            plan, mode = await Planner(provider=provider).plan(
                '不吃火锅了，换成KTV，人均100；如果有欢乐KTV也看看',
                context={'plan': {'category': '美食', 'keyword': '火锅'}})
            assert mode == 'model' and plan.category == 'KTV'
            assert plan.keyword == expected
        finally:
            await provider.close()
    asyncio.run(run())


@pytest.mark.parametrize('model_enabled', [False, True])
def test_pending_itinerary_does_not_duplicate_stops_or_leak_keyword(model_enabled):
    message = '两人先吃火锅再唱歌，总预算400元'

    def model(_request):
        return httpx.Response(200, json={'content': [{'type': 'text', 'text': json.dumps(
            {'category': '美食', 'keyword': '火锅', 'sort': 'score'})}]})

    async def run():
        provider = LLMProvider(LLMConfig(provider='anthropic', api_key='test' if model_enabled else '',
                                       base_url='https://example.test', model='test'),
                               httpx.MockTransport(model))
        try:
            plan, mode = await Planner(provider=provider).plan(
                message, context={'pending_user_messages': [message]})
            assert mode == ('model' if model_enabled else 'rules')
            assert [(s.category, s.keyword) for s in plan.stops] == [('美食', '火锅'), ('KTV', None)]
            assert plan.total_budget == 400 and plan.max_price is None
        finally:
            await provider.close()
    asyncio.run(run())


def test_pending_itinerary_keeps_turn_boundaries_and_latest_order():
    async def run():
        planner = Planner()
        try:
            plan, _ = await planner.plan('改成三个人，总预算还是400元', context={
                'pending_user_messages': ['两人先吃火锅再唱歌，总预算400元']})
            assert [(s.category, s.keyword) for s in plan.stops] == [('美食', '火锅'), ('KTV', None)]
            assert plan.party_size == 3
            changed, _ = await planner.plan('两人先唱歌然后再去吃火锅，总预算400元', context={
                'pending_user_messages': ['两人先吃火锅再唱歌，总预算400元']})
            assert [(s.category, s.keyword) for s in changed.stops] == [('KTV', None), ('美食', '火锅')]
        finally:
            await planner.provider.close()
    asyncio.run(run())


def test_rule_planner_examples():
    total, passed, failures = evaluate()
    assert passed == total, "\n".join(failures)


def test_model_plan_keeps_only_supported_search_terms():
    model_output = {
        "category": "餐厅", "keyword": "火锅店", "area": "编造地点", "max_price": 120,
        "min_score": 4.5, "wants_coupon": False, "nearby": False, "sort": "score",
    }

    def model(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"content": [{"type": "text", "text": json.dumps(model_output)}]})

    async def run():
        provider = LLMProvider(LLMConfig(provider="anthropic", api_key="test", model="test-model",
                                         base_url="https://example.test"), httpx.MockTransport(model))
        plan, mode = await Planner(provider=provider).plan("想找安静的火锅店，预算120，评分4.5")
        assert mode == "model"
        assert plan.category == "美食"
        assert plan.keyword == "火锅"
        assert plan.area is None
        assert plan.unverified_preferences == ["安静"]

    asyncio.run(run())


def test_model_cannot_set_internal_session_fields_and_trace_shows_sources():
    output = {"category": "美食", "keyword": "火锅", "max_price": 999,
              "excluded_shop_ids": [1, 2], "fixed_first_shop_id": 1,
              "second_price_ceiling": 1}

    def model(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"content": [{"type": "text", "text": json.dumps(output)}]})

    async def run():
        provider = LLMProvider(LLMConfig(provider="anthropic", api_key="test", model="test-model",
                                         base_url="https://example.test"), httpx.MockTransport(model))
        harness = AgentHarness({})
        plan, mode = await Planner(provider=provider).plan("找人均120以内的火锅", harness=harness)
        assert mode == "model"
        assert plan.max_price == 120
        assert plan.excluded_shop_ids == []
        assert plan.fixed_first_shop_id is None and plan.second_price_ceiling is None
        event = next(item for item in harness.events if item["event"] == "planner_model")
        assert event["field_sources"]["max_price"] == "rules"
        assert "excluded_shop_ids" in event["ignored_fields"]
    asyncio.run(run())


def test_model_relative_itinerary_distance_does_not_require_user_location():
    def model(_request):
        return httpx.Response(200, json={'content': [{'type': 'text', 'text': json.dumps(
            {'category': '美食', 'nearby': True, 'sort': 'distance'})}]})
    async def run():
        provider = LLMProvider(LLMConfig(provider='anthropic', api_key='test', model='test-model',
                                       base_url='https://example.test'), httpx.MockTransport(model))
        plan, mode = await Planner(provider=provider).plan('两个人先吃火锅再唱歌，总预算400元，尽量近')
        assert mode == 'model' and not plan.nearby and plan.sort == 'score'
        assert len(plan.stops) == 2
    asyncio.run(run())
