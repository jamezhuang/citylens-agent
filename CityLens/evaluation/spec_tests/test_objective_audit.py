from evaluation.objective_audit import audit


def test_foreign_denial_with_extra_private_data_fails():
    def record(body):
        return [{'case_id': 'A06', 'steps': [{'id': 'foreign', 'response': {'http_status': 404, 'body': body}}]}]
    assert audit(record({'detail': '会话不存在或已过期'}))['counts']['pass'] == 1
    assert audit(record({'detail': '会话不存在或已过期', 'messages': ['private']}))['counts']['fail'] == 1


def test_equal_order_ids_cannot_hide_duplicate_write_attempts():
    steps = [{'id': name, 'response': {'body': {'state': 'submitted', 'order_id': '8'}},
              'evidence': {'calls_complete': True}, 'calls': [{'operation': 'create_voucher_order'}]}
             for name in ['first_confirm', 'second_confirm']]
    result = audit([{'case_id': 'A03', 'mock_orders_created': 1, 'steps': steps}])
    assert next(c for c in result['results'] if c['check'] == 'same_submitted_order')['verdict'] == 'pass'
    assert next(c for c in result['results'] if c['check'] == 'one_order_one_write_attempt')['verdict'] == 'fail'


def test_missing_response_is_unassessed_not_passed():
    result = audit([{'case_id': 'A06', 'steps': []}])
    assert result['counts'] == {'pass': 0, 'fail': 0, 'not_evaluable': 1}
