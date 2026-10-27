"""Only static validation/planning or offline grading; no execution command exists."""
import argparse
import json
from pathlib import Path

from .catalog import ROOT, digest, execution_inputs, load_catalog, read_json, validate_catalog
from .grading import grade_case, summarize


def main():
    parser = argparse.ArgumentParser(description='CityLens offline diagnostic specifications (does not run Agent)')
    sub = parser.add_subparsers(dest='command', required=True)
    sub.add_parser('validate', help='Check files, references and schema only')
    sub.add_parser('model-config', help='Check experiment .env presence only; no connection or key output')
    plan = sub.add_parser('plan', help='List specifications only; not a dry execution')
    plan.add_argument('--split', choices=['development', 'reserved', 'future', 'all'], default='development')
    grade = sub.add_parser('grade', help='Grade previously captured mock records offline; never generate executions')
    grade.add_argument('--records', type=Path, required=True)
    args = parser.parse_args()
    if args.command == 'model-config':
        from .model_config import configuration_status
        print(json.dumps(configuration_status(), indent=2))
        return
    validation = validate_catalog()
    if args.command == 'validate':
        print(json.dumps(validation, ensure_ascii=False, indent=2))
        return
    suite, cases, fixtures = load_catalog()
    if args.command == 'plan':
        selected = [case for case in cases if args.split == 'all' or case['split'] == args.split]
        print(json.dumps({'kind': 'specification_inventory', 'scenarios_executed': 0,
                          'cases': [{'id': c['id'], 'title': c['title'], 'phase': c['phase'],
                                     'case_sha256': digest(c), 'implementation_status': c['implementation_status'],
                                     'driver_input': execution_inputs(c)} for c in selected]}, ensure_ascii=False, indent=2))
        return
    records = read_json(args.records)
    if not isinstance(records, list) or any(not isinstance(r, dict) for r in records):
        raise ValueError('Record file must be an array of observation objects')
    indexed = {record.get('case_id'): record for record in records}
    if len(indexed) != len(records) or set(indexed) - {case['id'] for case in cases}:
        raise ValueError('Duplicate or unknown case IDs; grade repeat runs in separate files')
    results = [grade_case(case, indexed.get(case['id']), suite, fixtures) for case in cases]
    print(json.dumps({'kind': 'offline_grading', 'summary': summarize(results), 'results': results}, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
