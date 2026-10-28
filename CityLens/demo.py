"""Reproducible end-to-end demo through the production ASGI application."""
import argparse
import asyncio
from copy import deepcopy
import json
from pathlib import Path
import sys

from evaluation.catalog import load_catalog
from evaluation.execution import RunBudget, execute_case


def reference(step, path):
    return {'$ref': {'step': step, 'path': 'response.body.' + path}}


def build_case():
    suite, cases, fixtures = load_catalog()
    index = {case['id']: case for case in cases}
    case = deepcopy(index['D01'])
    case['id'] = 'DEMO01'
    itinerary = deepcopy(index['P01']['steps'][0])
    itinerary['id'] = 'itinerary'
    itinerary['input']['session_id'] = reference('edit', 'session_id')
    actions = deepcopy(index['A03']['steps'])
    actions[0]['input']['session_id'] = reference('itinerary', 'session_id')
    case['steps'] += [itinerary] + actions
    case['steps'].append({'id': 'receipt', 'kind': 'query', 'actor': 'alice',
                          'input': {'operation': 'action_status',
                                    'action_id': reference('prepare', 'action_id')}})
    return suite, case, fixtures


def verify(record, fixtures):
    steps = {step['id']: step for step in record['steps']}
    def body(name):
        return steps[name]['response']['body']
    checks = {}
    checks['all_api_steps_succeeded'] = (record['execution_status'] == 'executed'
        and len(steps) == 8 and all(s['response']['http_status'] == 200 for s in steps.values()))
    if not checks['all_api_steps_succeeded']:
        return checks
    checks['same_session'] = len({body(k)['session_id'] for k in ['first','edit','itinerary','find']}) == 1
    shops = body('edit').get('shops', [])
    facts = fixtures['worlds']['base']['shops']
    checks['budget_and_category_retained'] = (body('edit')['plan']['max_price'] == 100
        and body('edit')['plan']['keyword'] == '火锅' and bool(shops)
        and all(facts[str(s['id'])]['avgPrice'] <= 100 and facts[str(s['id'])]['typeId'] == 1 for s in shops))
    from evaluation.grading import check_assertion
    suite, cases, _ = load_catalog()
    p = deepcopy(next(c for c in cases if c['id'] == 'P01'))
    itinerary = deepcopy(steps['itinerary'])
    itinerary['id'] = 'ask'
    check = next(c for c in p['assertions'] if c['op'] == 'itinerary_constraints')
    verdict, _ = check_assertion(check, itinerary, {'ask': itinerary}, fixtures['worlds']['base'], {})
    checks['two_stop_budget_verified'] = verdict == 'pass'
    before = record['steps'][:5]
    checks['no_order_before_confirmation'] = (body('prepare')['state'] == 'prepared'
        and not any(c['kind'] == 'write' for s in before for c in s['calls']))
    writes = [c for s in record['steps'] for c in s['calls'] if c['operation'] == 'create_voucher_order']
    checks['duplicate_confirmation_creates_one_order'] = (len(writes) == 1 and record['mock_orders_created'] == 1
        and body('first_confirm')['order_id'] == body('second_confirm')['order_id'])
    checks['receipt_matches_order'] = body('receipt')['order_id'] == body('first_confirm')['order_id']
    return checks


async def run(args):
    suite, case, fixtures = build_case()
    transcript = ['# CityLens 完整案例执行记录', '',
                  '规则 Agent · 真实 FastAPI/Workflow · 合成商户与内存订单 · 无模型调用', '']
    async def show(step, observation):
        heading = step['id'] + ': ' + step['input'].get('message', step['input'].get('operation', ''))
        body = observation['response']['body']
        calls = ', '.join(c['operation'] for c in observation['calls']) or '(无业务 HTTP 调用)'
        block = ['## ' + heading, '', '工具调用：' + calls, '', '```json',
                 json.dumps(body, ensure_ascii=False, indent=2), '```', '']
        transcript.extend(block)
        print('\n'.join(block), flush=True)
        if args.interactive and step['id'] == 'prepare':
            answer = input('仅提交内存演示订单。输入 yes 确认领券，其他输入停止：')
            if answer.strip().lower() != 'yes':
                raise RuntimeError('Viewer declined mock confirmation')
    record = await execute_case(case, suite, fixtures, 'mock_rules', RunBudget(), on_step=show)
    checks = verify(record, fixtures)
    passed = all(checks.values()) and len(checks) == 7
    summary = {'mode': 'mock_rules', 'business_transport': 'in_memory_mock_only',
               'model_calls': 0, 'real_orders_submitted': False, 'passed': passed, 'checks': checks}
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output/'records.json').write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding='utf-8')
    (args.output/'summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding='utf-8')
    transcript += ['## 验收', '', '```json', json.dumps(summary, ensure_ascii=False, indent=2), '```']
    (args.output/'transcript.md').write_text('\n'.join(transcript), encoding='utf-8')
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print('Artifacts:', args.output.resolve())
    return 0 if passed else 1


def main():
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--interactive', action='store_true', help='Pause before confirming the mock order')
    parser.add_argument('--output', type=Path, default=Path('reports/demo'))
    args = parser.parse_args()
    raise SystemExit(asyncio.run(run(args)))


if __name__ == '__main__':
    main()
