"""Read and statically validate versioned scenario specifications, without I/O to services."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent
OPERATORS = {'equals', 'one_of', 'count', 'shop_constraints', 'itinerary_constraints',
             'relative_cheaper', 'same_first_stop', 'call_count', 'manual'}
PHASES = {'core', 'future'}
SPLITS = {'development', 'reserved', 'future'}
STEP_OPERATIONS = {'action': {'prepare_voucher', 'confirm_action', 'delete_memory'},
                   'query': {'session_messages', 'action_status'},
                   'control': {'set_world', 'advance_clock'}}
FAULT_EFFECTS = {'return_world', 'timeout', 'business_rejected', 'http_error', 'forced_shop_ids',
                 'wait_for_cancellation', 'partial_result_deadline', 'commit_then_timeout'}


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     separators=(',', ':')).encode('utf-8')).hexdigest()


def read_json(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def load_catalog(root=ROOT):
    manifest = read_json(root / 'suite.json')
    cases = []
    for name in manifest['case_files']:
        path = (root / name).resolve()
        if not path.is_relative_to(root.resolve()):
            raise ValueError('Catalog path escapes evaluation directory')
        cases.extend(read_json(path))
    fixtures = read_json(root / manifest['fixture_file'])
    return manifest, cases, fixtures


def validate_catalog(root=ROOT):
    manifest, cases, fixtures = load_catalog(root)
    ids = set()
    for case in cases:
        missing = {'id', 'title', 'group', 'phase', 'split', 'objective', 'setup', 'steps',
                   'assertions', 'forbidden', 'failure_notes', 'implementation_status'} - set(case)
        if missing:
            raise ValueError(f"{case.get('id')}: missing {sorted(missing)}")
        if case['id'] in ids:
            raise ValueError('Duplicate case ID: ' + case['id'])
        ids.add(case['id'])
        if case['phase'] not in PHASES or case['split'] not in SPLITS:
            raise ValueError('Unknown phase/split: ' + case['id'])
        if case['implementation_status'] != 'specified_adapter_pending':
            raise ValueError('Catalog must not claim scenarios have executed')
        if case['setup']['fixture'] not in fixtures['worlds']:
            raise ValueError('Unknown fixture: ' + case['id'])
        if not case['steps'] or not case['assertions']:
            raise ValueError('Empty scenario: ' + case['id'])
        steps = {step['id'] for step in case['steps']}
        if len(steps) != len(case['steps']):
            raise ValueError('Duplicate step: ' + case['id'])
        assertion_ids = set()
        for step in case['steps']:
            if step['actor'] not in fixtures['actors'] or step['kind'] not in {'chat', 'action', 'control', 'query'}:
                raise ValueError('Unknown actor/step kind: ' + case['id'])
            if not isinstance(step.get('input'), dict):
                raise ValueError('Step input must be an object')
            if step['kind'] == 'chat':
                if not isinstance(step['input'].get('message'), str) or not step['input']['message'].strip():
                    raise ValueError('Chat step needs nonempty user message')
            elif step['input'].get('operation') not in STEP_OPERATIONS[step['kind']]:
                raise ValueError('Unknown driver operation: ' + case['id'])
        for fault in case['setup'].get('faults', []):
            if fault.get('effect') not in FAULT_EFFECTS or not fault.get('operation'):
                raise ValueError('Unknown fault injection: ' + case['id'])
            if fault.get('world') and fault['world'] not in fixtures['worlds']:
                raise ValueError('Unknown fault world: ' + case['id'])
        for check in case['assertions']:
            if check['id'] in assertion_ids or check['op'] not in OPERATORS:
                raise ValueError('Unknown or duplicate assertion: ' + case['id'])
            assertion_ids.add(check['id'])
            if check['step'] not in steps or check['severity'] not in {'blocking', 'quality'}:
                raise ValueError('Invalid assertion target: ' + case['id'])
            if not check.get('why'):
                raise ValueError('Assertion needs rationale: ' + case['id'])
            if check.get('world', case['setup']['fixture']) not in fixtures['worlds']:
                raise ValueError('Unknown assertion oracle world: ' + case['id'])
            if check['op'] == 'manual' and not check.get('rubric'):
                raise ValueError('Manual review requires explicit rubric')
            if check['op'] in {'equals', 'one_of', 'count'} and not check.get('path'):
                raise ValueError('Assertion needs a record path')
            if check['op'] == 'one_of' and not check.get('values'):
                raise ValueError('one_of needs allowed values')
            if check['op'] == 'equals' and 'value' not in check:
                raise ValueError('equals needs an explicit expected value, including null')
            if check['op'] in {'relative_cheaper', 'same_first_stop'}:
                ordered = [step['id'] for step in case['steps']]
                previous = check.get('previous_step')
                if previous not in ordered or ordered.index(previous) >= ordered.index(check['step']):
                    raise ValueError('Cross-turn check must reference an earlier step')
            if check['op'] in {'count', 'call_count'}:
                if not ({'min', 'max'} & set(check)):
                    raise ValueError('Count check needs a bound')
                if any(type(check[key]) is not int or check[key] < 0 for key in ('min', 'max') if key in check):
                    raise ValueError('Count bounds must be nonnegative integers')
                if check.get('min', 0) > check.get('max', float('inf')):
                    raise ValueError('Count bounds are reversed')
        for document in case['setup'].get('documents', []):
            if document not in fixtures['documents']:
                raise ValueError('Unknown document fixture: ' + case['id'])
        for condition in case['setup'].get('preconditions', []):
            if condition.get('op') != 'cheaper_candidate_exists' or condition.get('previous_step') not in steps:
                raise ValueError('Invalid scenario precondition: ' + case['id'])
        if case['phase'] == 'future' and case['split'] != 'future':
            raise ValueError('Future scenarios must not enter baseline denominator')
    core = [case for case in cases if case['phase'] == 'core']
    if len(core) != manifest['expected_core_cases'] or len(cases) - len(core) != manifest['expected_future_cases']:
        raise ValueError('Scenario counts do not match manifest')
    # References are data lookups only, never Python expressions.
    for case in cases:
        seen = set()
        for step in case['steps']:
            def references(value):
                if isinstance(value, dict):
                    if '$ref' in value:
                        ref = value['$ref']
                        if set(value) != {'$ref'} or ref.get('step') not in seen or not ref.get('path'):
                            raise ValueError('Reference must target a previous step: ' + case['id'])
                    else:
                        for child in value.values():
                            references(child)
                elif isinstance(value, list):
                    for child in value:
                        references(child)
            references(step['input'])
            seen.add(step['id'])
    return {'validation': 'specification_valid', 'suite_version': manifest['version'],
            'core_cases': len(core), 'future_cases': len(cases) - len(core),
            'groups': {group: sum(c['group'] == group for c in core) for group in sorted({c['group'] for c in core})},
            'suite_sha256': digest(cases), 'fixture_sha256': digest(fixtures),
            'scenarios_executed': 0, 'model_calls': 0, 'business_requests': 0}


def execution_inputs(case):
    """Driver inputs only. Expected answers/rubrics must not reach the task Agent."""
    return {'case_id': case['id'], 'setup': case['setup'], 'steps': case['steps']}
