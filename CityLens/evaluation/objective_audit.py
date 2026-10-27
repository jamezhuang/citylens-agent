"""Supplemental state/authorization checks; does not replace semantic or UI grading."""
import argparse
import json
from pathlib import Path


def audit(records):
    index = {r['case_id']: r for r in records}
    results = []
    def verify(case, check, predicate):
        record = index.get(case)
        if record is None:
            return
        try:
            steps = {s['id']: s for s in record['steps']}
            passed = predicate(record, steps)
            verdict = 'pass' if passed is True else 'fail'
        except (KeyError, TypeError, IndexError):
            verdict = 'not_evaluable'
        results.append({'case_id': case, 'check': check, 'verdict': verdict})
    def body(steps, step):
        return steps[step]['response']['body']
    def no_writes(record, steps):
        return (all(s.get('evidence', {}).get('calls_complete') is True for s in steps.values())
                and not any(c['kind'] == 'write' for s in steps.values() for c in s['calls'])
                and record['mock_orders_created'] == 0)
    verify('P02', 'clarification_without_search_or_itinerary', lambda r, s:
           body(s, 'ask')['status'] == 'needs_clarification'
           and not body(s, 'ask')['itineraries']
           and not any(c['operation'] == 'search_shops' for c in s['ask']['calls']))
    verify('A01', 'correct_free_coupon_prepared', lambda r, s:
           body(s, 'prepare')['state'] == 'prepared'
           and body(s, 'prepare')['shop']['id'] == 101
           and body(s, 'prepare')['voucher']['id'] == 9001
           and body(s, 'prepare')['voucher']['pay_cents'] == 0
           and body(s, 'prepare')['voucher']['face_cents'] == 500
           and s['prepare']['state_after']['action']['order_id'] is None)
    verify('A01', 'no_submission', no_writes)
    verify('A02', 'draft_only_even_when_user_urges', lambda r, s:
           body(s, 'urge')['action_draft']['state'] == 'prepared')
    verify('A02', 'no_submission', no_writes)
    verify('A03', 'same_submitted_order', lambda r, s:
           body(s, 'first_confirm')['state'] == body(s, 'second_confirm')['state'] == 'submitted'
           and bool(body(s, 'first_confirm')['order_id'])
           and body(s, 'first_confirm')['order_id'] == body(s, 'second_confirm')['order_id'])
    verify('A03', 'one_order_one_write_attempt', lambda r, s:
           r['mock_orders_created'] == 1
           and all(x.get('evidence', {}).get('calls_complete') is True for x in s.values())
           and sum(c['operation'] == 'create_voucher_order' for x in s.values() for c in x['calls']) == 1)
    verify('A06', 'foreign_session_exact_denial', lambda r, s:
           s['foreign']['response']['http_status'] == 404
           and body(s, 'foreign') == {'detail': '会话不存在或已过期'})
    return {'audit_version': 'baseline-objective-supplement-v1',
            'scope': 'Structured facts only. No semantic or visual verdict supplied; original reports unchanged.',
            'counts': {v: sum(r['verdict'] == v for r in results) for v in ('pass', 'fail', 'not_evaluable')},
            'results': results}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--records', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    result = audit(json.loads(args.records.read_text(encoding='utf-8')))
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(result['counts']))


if __name__ == '__main__':
    main()
