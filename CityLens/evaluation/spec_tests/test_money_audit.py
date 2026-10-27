from evaluation.money_audit import audit_step


def test_independent_money_audit_detects_hundredfold_error():
    fixture = {'vouchers': {'9': {'title': '免费券', 'payValue': 0, 'actualValue': 500}}}
    def check(text):
        return audit_step({'response': {'body': {'answer': text, 'shops': [
            {'coupons': [{'id': 9}]}]}}}, fixture)[0]['verdict']
    assert check('免费券：售价 ¥0.00，面额 ¥5.00') == 'pass'
    assert check('免费券（面值 ¥500）') == 'fail'
    assert check('查到了免费券') == 'not_evaluable'


def test_two_coupon_amounts_on_same_line_do_not_contaminate_each_other():
    fixture = {'vouchers': {'1': {'title': '免费券', 'payValue': 0, 'actualValue': 500},
                            '2': {'title': '付费券', 'payValue': 8000, 'actualValue': 10000}}}
    def check(text):
        return audit_step({'response': {'body': {'answer': text, 'shops': [
            {'coupons': [{'id': 1}, {'id': 2}]}]}}}, fixture)
    assert [r['verdict'] for r in check('免费券（售价¥0，面额¥5）；付费券（售价¥80，面额¥100）')] == ['pass', 'pass']
    assert [r['verdict'] for r in check('免费券（售价¥0，面额¥500）；付费券（售价¥80，面额¥100）')] == ['fail', 'pass']
