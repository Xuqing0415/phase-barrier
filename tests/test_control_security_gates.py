"""The security harness must reject escapes, incomplete probes and bad evidence."""
import pytest

from benchmarks.control_red_team.base import ControlAttempt
from benchmarks.control_red_team.run_control_red_team import main
from benchmarks.red_team.techniques.base import Outcome
from scripts.check_control_coverage import check


@pytest.mark.parametrize('kind,expected', [('assert', 'escaped'), ('error', 'inconclusive')])
def test_harness_does_not_call_failures_blocked(tmp_path, kind, expected):
    def probe(env):
        if kind == 'assert':
            raise AssertionError('forbidden operation succeeded')
        raise OSError('infrastructure failure')
    attempt = ControlAttempt(technique='control_bypass', target_defense=4,
                             requirement='no bypass', goal_stage=6, probe=probe)
    result = attempt.run(tmp_path)
    assert result.outcome.value == expected


@pytest.mark.parametrize('outcome', [Outcome.ESCAPED, Outcome.INCONCLUSIVE])
def test_control_cli_is_red_for_escapes_or_incomplete_probes(tmp_path, monkeypatch, outcome):
    from benchmarks.red_team.run_red_team import RedTeamAgent
    from benchmarks.red_team.techniques.base import EscapeResult
    row = EscapeResult(technique='control_bypass', case_id='failure', target_defense=4,
                        expectation='partial', outcome=outcome, goal_stage=6,
                        stage_reached=1, message='injected')
    monkeypatch.setattr(RedTeamAgent, 'run', lambda *_: [row])
    assert main(['--workspace', str(tmp_path), '--json']) == 1


def test_control_waiver_requires_expiry_and_expired_entries_fail(tmp_path, monkeypatch):
    from benchmarks.red_team.run_red_team import RedTeamAgent
    monkeypatch.setattr(RedTeamAgent, 'run', lambda *_: [])
    ledger = tmp_path / 'waivers.yaml'
    ledger.write_text('version: 1\nentries:\n- technique: control_bypass\n  case_id: old\n  reason: explicit\n', encoding='utf-8')
    assert main(['--workspace', str(tmp_path), '--known-residual', str(ledger)]) == 2
    ledger.write_text(ledger.read_text(encoding='utf-8') + '  expires_at: 2000-01-01\n', encoding='utf-8')
    assert main(['--workspace', str(tmp_path), '--known-residual', str(ledger)]) == 1


def evidence():
    summary = {'num_statements': 100, 'num_branches': 100, 'covered_lines': 97, 'covered_branches': 97}
    return {'meta': {'branch_coverage': True}, 'files': {
        f'anti_shortcut/{name}.py': {'summary': dict(summary)}
        for name in ['skill', 'control/permissions', 'control/service', 'control/tui']}}


def test_control_coverage_gate_rejects_local_gaps_and_missing_measurements():
    data = evidence()
    assert check(data)[1] == []
    data['files']['anti_shortcut/control/service.py']['summary']['covered_branches'] = 92
    failures = check(data)[1]
    assert 'service < 95%' in failures
    data = evidence()
    data['files']['anti_shortcut/skill.py']['summary']['covered_lines'] = 100
    data['files']['anti_shortcut/skill.py']['summary']['covered_branches'] = 100
    assert 'control < max(90%, core)' in check(data)[1]
    for invalid in [{}, {'meta': {'branch_coverage': False}},
                    {'meta': {'branch_coverage': True}, 'files': {}}]:
        with pytest.raises((ValueError, KeyError)):
            check(invalid)
