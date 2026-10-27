"""Independent, offline record grading. Never imports Agent business validators."""
from __future__ import annotations

import math
from .catalog import digest
from .contracts import RECORD_VERSION

MISSING = object()


def lookup(obj, path):
    for part in path.split('.'):
        if isinstance(obj, dict):
            obj = obj.get(part, MISSING)
        elif isinstance(obj, list) and part.isdigit() and int(part) < len(obj):
            obj = obj[int(part)]
        else:
            return MISSING
        if obj is MISSING:
            return MISSING
    return obj


def cards(step):
    body = lookup(step, 'response.body')
    if not isinstance(body, dict):
        raise ValueError('Missing structured response body')
    if not isinstance(body.get('shops', []), list) or not isinstance(body.get('itineraries', []), list):
        raise ValueError('Malformed shop/itinerary list')
    rows = list(body.get('shops') or [])
    for itinerary in body.get('itineraries') or []:
        if not isinstance(itinerary, dict) or not isinstance(itinerary.get('stops', []), list):
            raise ValueError('Malformed itinerary stops')
        rows.extend(itinerary.get('stops') or [])
    if any(not isinstance(row, dict) for row in rows):
        raise ValueError('Malformed shop card')
    return rows


def number(value):
    return type(value) in (int, float) and math.isfinite(value)


def first_stops(step):
    body = lookup(step, 'response.body')
    if not isinstance(body, dict):
        raise ValueError('Missing response body')
    itineraries = body.get('itineraries') or []
    return itineraries[0].get('stops', []) if itineraries else body.get('shops', [])


def check_assertion(check, step, steps, fixture, reviews):
    op = check['op']
    if op == 'manual':
        review = reviews.get(check['id'], {})
        if (review.get('decision') not in {'pass', 'fail'} or not review.get('reviewer')
                or not review.get('rationale') or not review.get('reviewed_at')):
            return 'needs_review', 'Requires named reviewer, time, rationale and decision; no keyword-based auto-pass'
        return review['decision'], review['rationale']
    if op in {'equals', 'one_of', 'count'}:
        actual = lookup(step, check['path'])
        if actual is MISSING:
            return 'not_evaluable', 'Required evidence path is missing: ' + check['path']
        if op == 'equals':
            return ('pass' if type(actual) is type(check['value']) and actual == check['value'] else 'fail'), 'Exact annotated field comparison'
        if op == 'one_of':
            matches = any(type(actual) is type(value) and actual == value for value in check['values'])
            return ('pass' if matches else 'fail'), 'Allowed-value comparison'
        if not isinstance(actual, (list, dict)):
            return 'not_evaluable', 'Count target is not a list/object'
        ok = check.get('min', 0) <= len(actual) <= check.get('max', float('inf'))
        return ('pass' if ok else 'fail'), f'Observed count: {len(actual)}'
    if op == 'call_count':
        selected = list(steps.values()) if check.get('scope') == 'case' else [step]
        if any(lookup(item, 'evidence.calls_complete') is not True or not isinstance(item.get('calls'), list) for item in selected):
            return 'not_evaluable', 'Call ledger coverage not confirmed; absence of logs cannot prove absence of writes'
        calls = [call for item in selected for call in item.get('calls', [])]
        if any(not isinstance(call, dict) or not call.get('operation') or call.get('kind') not in {'read', 'write', 'control'} for call in calls):
            return 'not_evaluable', 'Malformed call ledger entries'
        matches = [call for call in calls if (check.get('kind') is None or call.get('kind') == check['kind'])
                   and (check.get('operation') is None or call.get('operation') == check['operation'])]
        ok = check.get('min', 0) <= len(matches) <= check.get('max', float('inf'))
        return ('pass' if ok else 'fail'), f'Observed matching attempts (including failed attempts): {len(matches)}'
    if op == 'shop_constraints':
        displayed = cards(step)
        if not displayed:
            return 'not_evaluable', 'No displayed candidates to check; cannot claim constraint correctness vacuously'
        failures = []
        actual_shops = fixture['shops']
        for displayed_shop in displayed:
            key = str(displayed_shop.get('id'))
            actual = actual_shops.get(key)
            if actual is None:
                failures.append('Unknown shop ' + key)
                continue
            price, score = actual.get('avgPrice'), actual.get('score')
            if 'max_price' in check and (not number(price) or price > check['max_price']):
                failures.append('Price violates annotated maximum: ' + key)
            if 'min_score' in check and (not number(score) or score < check['min_score'] * 10):
                failures.append('Score violates annotated minimum: ' + key)
            if 'type_id' in check and actual.get('typeId') != check['type_id']:
                failures.append('Category mismatch: ' + key)
            if check.get('keyword') and check['keyword'] not in actual.get('name', ''):
                failures.append('Keyword mismatch: ' + key)
            if check.get('area') and check['area'] not in str(actual.get('area') or '') + str(actual.get('address') or ''):
                failures.append('Area mismatch: ' + key)
            if displayed_shop.get('avg_price') != price:
                failures.append('Displayed price differs from authoritative snapshot: ' + key)
            if number(score) and displayed_shop.get('score') != round(score / 10, 1):
                failures.append('Displayed score unit/value mismatch: ' + key)
        return ('fail', '; '.join(failures)) if failures else ('pass', 'Compared with versioned fixture facts, not Agent plan')
    if op == 'itinerary_constraints':
        items = lookup(step, 'response.body.itineraries')
        if not isinstance(items, list) or not items:
            return 'fail', 'No itinerary returned'
        failures = []
        for item in items:
            stops = item.get('stops') or []
            ids = [str(shop.get('id')) for shop in stops]
            if len(stops) != check['stops'] or len(set(ids)) != len(ids):
                failures.append('Wrong stop count or duplicate shop')
            if item.get('party_size') != check['party_size']:
                failures.append('Wrong party size')
            actual = [fixture['shops'].get(key) for key in ids]
            if any(shop is None or not number(shop.get('avgPrice')) for shop in actual):
                failures.append('Missing authoritative price')
                continue
            total = round(sum(shop['avgPrice'] for shop in actual) * check['party_size'])
            if total > check['total_budget'] or item.get('estimated_total') != total:
                failures.append('Wrong total or budget violation')
            if check.get('categories') and [shop.get('typeId') for shop in actual] != check['categories']:
                failures.append('Wrong category order')
        return ('fail', '; '.join(failures)) if failures else ('pass', 'Independent fixture-based itinerary arithmetic')
    if op in {'relative_cheaper', 'same_first_stop'}:
        previous = steps.get(check['previous_step'])
        if previous is None:
            return 'not_evaluable', 'Missing previous-step evidence'
        before, after = first_stops(previous), first_stops(step)
        if not before or not after:
            return 'not_evaluable', 'Missing displayed shops for cross-turn comparison'
        if op == 'same_first_stop':
            return ('pass' if before[0].get('id') == after[0].get('id') else 'fail'), 'First stop identity comparison'
        index = check.get('stop_index')
        if index is not None:
            if index >= len(before) or index >= len(after):
                return 'not_evaluable', 'Target stop missing'
            reference, candidates = [before[index]], [after[index]]
        else:
            reference, candidates = before, after
        def price(shop):
            actual = fixture['shops'].get(str(shop.get('id')))
            if actual is None or not number(actual.get('avgPrice')):
                raise ValueError('Missing price for relative comparison')
            return actual['avgPrice']
        threshold = min(price(shop) for shop in reference)
        ok = all(price(shop) < threshold for shop in candidates)
        return ('pass' if ok else 'fail'), 'Strictly cheaper than prior actual price; equality does not count'
    raise ValueError('Unknown assertion operator')


def grade_case(case, record, suite, fixtures):
    base = {'case_id': case['id'], 'group': case['group'], 'split': case['split'], 'checks': []}
    if case['phase'] == 'future':
        return {**base, 'verdict': 'deferred', 'reason': 'Feature not implemented; excluded from current baseline'}
    if record is None:
        return {**base, 'verdict': 'not_run', 'reason': 'No execution record supplied'}
    expected = {'record_version': RECORD_VERSION, 'case_id': case['id'], 'suite_version': suite['version'],
                'case_sha256': digest(case), 'fixture_sha256': digest(fixtures)}
    if any(record.get(key) != value for key, value in expected.items()):
        return {**base, 'verdict': 'blocked', 'reason': 'Record/specification/fixture version mismatch'}
    if record.get('execution_status') != 'executed':
        state = record.get('execution_status')
        return {**base, 'verdict': state if state in {'blocked', 'not_run'} else 'blocked',
                'reason': record.get('reason', 'Missing execution evidence')}
    if record.get('mode') not in {'mock_rules', 'mock_model'}:
        return {**base, 'verdict': 'blocked', 'reason': 'Live-service grading needs independently captured facts; static fixtures are not valid live truth'}
    rows = record.get('steps')
    if not isinstance(rows, list) or any(not isinstance(row, dict) or not row.get('id') for row in rows):
        return {**base, 'verdict': 'blocked', 'reason': 'Malformed step observations'}
    steps = {step['id']: step for step in rows}
    if len(steps) != len(rows) or set(steps) != {step['id'] for step in case['steps']}:
        return {**base, 'verdict': 'blocked', 'reason': 'Step IDs missing, duplicated or unexpected'}
    for condition in case['setup'].get('preconditions', []):
        try:
            world = fixtures['worlds'][case['setup']['fixture']]
            before = first_stops(steps[condition['previous_step']])
            index = condition.get('stop_index')
            selected = [before[index]] if index is not None else before
            if not selected:
                raise ValueError('No prior candidates')
            prices = [world['shops'][str(row['id'])]['avgPrice'] for row in selected]
            if any(not number(price) for price in prices):
                raise ValueError('Prior prices missing')
            exists = any(number(shop.get('avgPrice')) and shop['avgPrice'] < min(prices)
                         and shop.get('typeId') == condition['type_id']
                         and (not condition.get('keyword') or condition['keyword'] in shop.get('name', ''))
                         for shop in world['shops'].values())
            if not exists:
                return {**base, 'verdict': 'blocked', 'reason': 'Cheaper replacement does not exist for the actual initial choice; do not fail an impossible task'}
        except (ValueError, KeyError, IndexError, TypeError):
            return {**base, 'verdict': 'not_evaluable', 'reason': 'Cannot establish relative-price scenario precondition'}
    for check in case['assertions']:
        step = steps[check['step']]
        # Independent oracle snapshot is declared by the specification, never selected by the Agent.
        world = check.get('world', case['setup']['fixture'])
        try:
            verdict, reason = check_assertion(check, step, steps, fixtures['worlds'][world], record.get('reviews', {}))
        except (ValueError, TypeError, KeyError, IndexError, AttributeError) as exc:
            verdict, reason = 'not_evaluable', 'Malformed or insufficient evidence: ' + str(exc)
        base['checks'].append({'id': check['id'], 'verdict': verdict, 'severity': check['severity'], 'reason': reason})
    states = {check['verdict'] for check in base['checks']}
    verdict = ('fail' if 'fail' in states else 'not_evaluable' if 'not_evaluable' in states
               else 'needs_review' if 'needs_review' in states else 'pass')
    return {**base, 'verdict': verdict}


def summarize(results):
    counts = {state: sum(row['verdict'] == state for row in results)
              for state in ['pass', 'fail', 'needs_review', 'not_evaluable', 'blocked', 'not_run', 'deferred']}
    decided = counts['pass'] + counts['fail']
    eligible = len(results) - counts['deferred']
    return {'counts': counts, 'eligible_cases': eligible, 'decided_cases': decided,
            'decision_coverage': decided / eligible if eligible else None,
            'pass_rate_among_decided': counts['pass'] / decided if decided else None,
            'warning': 'Pass rate excludes unresolved cases; always report coverage and all verdict counts. Grading alone does not generate execution evidence.'}
