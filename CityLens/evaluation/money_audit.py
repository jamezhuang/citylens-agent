"""Supplement v1: narrow, independent check of named coupon amounts in Chinese replies.

Does not replace semantic review or modify the frozen core suite/old reports.
"""
import argparse
from decimal import Decimal
import json
from pathlib import Path
import re

from .catalog import load_catalog


def audit_step(step, fixture):
    body = step.get('response', {}).get('body', {})
    text = body.get('answer', '')
    rows = []
    seen = set()
    for shop in body.get('shops', []):
        for coupon in shop.get('coupons', []):
            key = str(coupon.get('id'))
            if key in seen:
                continue
            seen.add(key)
            actual = fixture.get('vouchers', {}).get(key)
            if not actual:
                rows.append({'voucher_id': key, 'verdict': 'fail', 'reason': 'Unknown fixture voucher'})
                continue
            title = actual['title']
            # Only named coupon clauses on a single line are parsed. Other
            # phrasing stays unassessed rather than passing by missing evidence.
            # One line can contain several coupons. A following coupon title
            # ends this coupon's amount scope, even before the newline.
            titles = sorted({v['title'] for v in fixture.get('vouchers', {}).values()}, key=len, reverse=True)
            pattern = '|'.join(re.escape(t) for t in titles)
            clauses = []
            for line in text.splitlines():
                boundaries = list(re.finditer(pattern, line))
                for i, boundary in enumerate(boundaries):
                    if boundary.group() == title:
                        end = boundaries[i + 1].start() if i + 1 < len(boundaries) else len(line)
                        clauses.append(line[boundary.end():end])
            mentions = []
            for clause in clauses:
                for label, amount in re.findall(r'(售价|实付|面额|面值|券面|抵扣)\s*[:：]?\s*[¥￥]?\s*(\d+(?:\.\d+)?)', clause):
                    field = 'payValue' if label in {'售价', '实付'} else 'actualValue'
                    expected = Decimal(actual[field]) / Decimal(100)
                    mentions.append({'field': field, 'yuan': amount, 'expected_yuan': str(expected),
                                     'matches': Decimal(amount) == expected})
            if any(not x['matches'] for x in mentions):
                verdict = 'fail'
            elif {x['field'] for x in mentions} == {'payValue', 'actualValue'}:
                verdict = 'pass'
            else:
                verdict = 'not_evaluable'
            rows.append({'voucher_id': key, 'verdict': verdict, 'mentions': mentions})
    return rows


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--records', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    _, cases, fixtures = load_catalog()
    specs = {case['id']: case for case in cases}
    rows = []
    for record in json.loads(args.records.read_text(encoding='utf-8')):
        fixture = fixtures['worlds'][specs[record['case_id']]['setup']['fixture']]
        for step in record['steps']:
            for row in audit_step(step, fixture):
                rows.append({'case_id': record['case_id'], 'step': step['id'], **row})
    report = {'audit_version': 'coupon-money-supplement-v2', 'source_records': str(args.records),
              'scope': 'Named coupon clauses only; initial fixture amounts; not full semantic grading',
              'counts': {v: sum(r['verdict'] == v for r in rows) for v in ('pass', 'fail', 'not_evaluable')},
              'results': rows}
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(report['counts']))


if __name__ == '__main__':
    main()
