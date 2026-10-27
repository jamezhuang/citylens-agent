"""Offline grader contracts; no model or real business services are used."""
from evaluation.catalog import digest
from evaluation.grading import grade_case, summarize


def data(check):
    case = {'id': 'X01', 'group': 'single', 'phase': 'core', 'split': 'development',
            'setup': {'fixture': 'base'}, 'steps': [{'id': 'ask'}], 'assertions': [check]}
    suite = {'version': 'unit'}
    fixtures = {'worlds': {'base': {'shops': {'1': {'id': 1, 'avgPrice': 180, 'score': 45}}}}}
    record = {'record_version': 1, 'case_id': 'X01', 'suite_version': 'unit', 'case_sha256': digest(case),
              'fixture_sha256': digest(fixtures), 'execution_status': 'executed', 'mode': 'mock_rules',
              'steps': [{'id': 'ask', 'response': {'body': {'shops': [{'id': 1, 'avg_price': 180, 'score': 4.5}],
                                                           'plan': {'max_price': 180}}}}]}
    return case, record, suite, fixtures


def test_no_record_is_not_a_pass():
    case, _, suite, fixtures = data({'id': 'a', 'step': 'ask', 'op': 'equals', 'severity': 'blocking', 'path': 'outcome', 'value': 'success'})
    assert grade_case(case, None, suite, fixtures)['verdict'] == 'not_run'


def test_oracle_uses_annotated_budget_not_agent_plan():
    case, record, suite, fixtures = data({'id': 'a', 'step': 'ask', 'op': 'shop_constraints', 'severity': 'blocking', 'max_price': 150})
    assert grade_case(case, record, suite, fixtures)['verdict'] == 'fail'


def test_missing_call_ledger_does_not_prove_no_writes():
    case, record, suite, fixtures = data({'id': 'a', 'step': 'ask', 'op': 'call_count', 'severity': 'blocking', 'kind': 'write', 'max': 0})
    assert grade_case(case, record, suite, fixtures)['verdict'] == 'not_evaluable'


def test_missing_manual_review_never_auto_passes():
    case, record, suite, fixtures = data({'id': 'a', 'step': 'ask', 'op': 'manual', 'severity': 'quality', 'rubric': 'Check factual disclosure'})
    assert grade_case(case, record, suite, fixtures)['verdict'] == 'needs_review'


def test_stale_fixture_hash_is_blocked():
    case, record, suite, fixtures = data({'id': 'a', 'step': 'ask', 'op': 'equals', 'severity': 'blocking', 'path': 'outcome', 'value': 'success'})
    record['fixture_sha256'] = 'wrong'
    assert grade_case(case, record, suite, fixtures)['verdict'] == 'blocked'


def test_live_records_cannot_be_graded_against_static_world():
    case, record, suite, fixtures = data({'id': 'a', 'step': 'ask', 'op': 'equals', 'severity': 'blocking', 'path': 'outcome', 'value': 'success'})
    record['mode'] = 'lab_model'
    assert grade_case(case, record, suite, fixtures)['verdict'] == 'blocked'


def test_unexecuted_suite_has_no_success_rate():
    result = summarize([{'verdict': 'not_run'}, {'verdict': 'deferred'}])
    assert result['pass_rate_among_decided'] is None
    assert result['decision_coverage'] == 0
