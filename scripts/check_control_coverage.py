"""Fail on control regressions using exact combined line/branch coverage."""
from __future__ import annotations

import argparse
import json
from pathlib import Path


def percent(summaries):
    total = sum(s['num_statements'] + s['num_branches'] for s in summaries)
    covered = sum(s['covered_lines'] + s['covered_branches'] for s in summaries)
    if not total:
        raise ValueError('No executable lines/branches in coverage input')
    return 100 * covered / total


def check(data):
    if not data['meta'].get('branch_coverage'):
        raise ValueError('Branch coverage is required')
    files = {key.replace('\\', '/'): value['summary'] for key, value in data['files'].items()}
    core = [v for k, v in files.items() if k.startswith('anti_shortcut/')]
    control = [v for k, v in files.items() if k.startswith('anti_shortcut/control/')]
    if not any(k.startswith('anti_shortcut/') and '/control/' not in k for k in files):
        raise ValueError('Measure the whole core package to compare control with core')
    measured = {'core': percent(core), 'control': percent(control)}
    for name in ['permissions', 'service']:
        measured[name] = percent([files[f'anti_shortcut/control/{name}.py']])
    failures = []
    if measured['core'] < 90:
        failures.append('core < 90%')
    if measured['control'] < max(90, measured['core']):
        failures.append('control < max(90%, core)')
    for name in ['permissions', 'service']:
        if measured[name] < 95:
            failures.append(f'{name} < 95%')
    return measured, failures


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('coverage_json', type=Path)
    args = parser.parse_args(argv)
    try:
        measured, failures = check(json.loads(args.coverage_json.read_text(encoding='utf-8')))
    except (ValueError, KeyError, OSError) as exc:
        print(f'Invalid coverage evidence: {exc}')
        return 2
    print(json.dumps({'percent_line_and_branch': measured, 'failures': failures}, indent=2))
    return 1 if failures else 0


if __name__ == '__main__':
    raise SystemExit(main())
