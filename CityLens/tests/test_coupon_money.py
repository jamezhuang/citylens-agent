import asyncio
from types import SimpleNamespace

import pytest

from citylens.dialogue import route_message
from citylens.llm import Completion
from citylens.money import coupon_observation
from citylens.planner import Planner
from citylens.protocol import ToolCall
from citylens.schemas import ChatRequest, ChatResponse, ShopCard
from citylens.workflow import DiscoveryWorkflow


@pytest.mark.parametrize('cents,expected', [(0, '0.00'), (1, '0.01'), (500, '5.00'),
                                          (8999, '89.99'), (10000, '100.00'),
                                          (None, None), (True, None), (-1, None), ('500', None)])
def test_model_money_contract_uses_explicit_yuan(cents, expected):
    observation = coupon_observation({'payValue': cents, 'actualValue': cents})
    assert observation['pay_yuan'] == observation['face_yuan'] == expected
    assert observation['currency'] == 'CNY'
    assert 'payValue' not in observation and 'actualValue' not in observation


def test_wrong_model_coupon_amount_cannot_reach_final_answer():
    async def run():
        workflow = DiscoveryWorkflow(None, Planner(), 'http://test')
        class Model:
            enabled = True
            config = SimpleNamespace(timeout=1, provider='test', model='test')
            calls = 0
            async def complete(self, *args, **kwargs):
                self.calls += 1
                if self.calls == 1:
                    return Completion(tool_calls=[ToolCall('lookup', 'run_workflow', {})])
                return Completion(text='测试免费券面额500元，已经领取。')
        workflow.planner.provider = Model()
        async def execute(harness):
            return ChatResponse(answer='查到券信息，尚未领取。', status='ok', mode='rules',
                shops=[ShopCard(id=1, name='甲店', url='http://test/1', reason='test', coupons=[
                    {'id': 9, 'title': '测试免费券', 'payValue': 0, 'actualValue': 500}])])
        result, _ = await route_message(ChatRequest(message='查券'), workflow, 7, execute_workflow=execute)
        assert '售价 ¥0.00，面额 ¥5.00' in result.answer
        assert '500元' not in result.answer and '已经领取' not in result.answer
        assert result.shops[0].coupons[0]['actualValue'] == 500
    asyncio.run(run())
