"""Adversarial control-plane boundary regressions, independent of UI checks."""
import hashlib
import json

import pytest

from anti_shortcut import AntiShortcutSkill
from anti_shortcut.proxy import GateProxy, WriteDenied
from benchmarks.red_team.techniques import ALL_TECHNIQUES
from benchmarks.control_red_team.base import ControlTechnique

ATTEMPTS = [attempt for technique in ALL_TECHNIQUES if isinstance(technique, ControlTechnique)
            for attempt in technique.generate_all('control security')]


@pytest.mark.parametrize('attempt', ATTEMPTS, ids=lambda a: f'{a.technique}/{a.case_id}')
def test_adversarial_control_boundary(tmp_path, attempt):
    result = attempt.run(tmp_path)
    assert result.outcome.value == 'blocked', result.to_row()


@pytest.fixture
def control(tmp_path):
    skill = AntiShortcutSkill(tmp_path)
    token = skill.control.permissions.bootstrap()
    with skill.control.permissions.session(token):
        yield skill
    skill.close()


@pytest.mark.parametrize('broken', ['malformed', 'missing'])
def test_break_glass_cannot_bypass_broken_policy(control, broken):
    observer = control.control.permissions.issue('observer')
    token = control.control.break_glass_issue('docs/note.md', observer['session_id'], 'repair')['token']
    if broken == 'missing':
        control.control.permissions.path.unlink()
    else:
        control.control.permissions.path.write_text('roles: [', encoding='utf-8')
    with control.control.permissions.session(observer['token']), pytest.raises(WriteDenied, match='策略'):
        GateProxy(control).write_file('docs/note.md', 'must not exist', break_glass=token)
    assert not (control.workspace / 'docs/note.md').exists()


@pytest.mark.parametrize('pattern', ['', '../*', '/tmp/*', 'C:/tmp/*', 'src\\*', 'src/[abc',
                                     'src/[]', 'src/[!]', 'src/abc]', ' src/*'])
def test_invalid_path_patterns_are_rejected(control, pattern):
    for field in ['write_paths', 'review_writes']:
        value = {'worker': [pattern]} if field == 'write_paths' else [pattern]
        with pytest.raises(ValueError, match='pattern'):
            control.control.permissions.update({'version': 1, field: value})


def test_break_glass_failed_write_is_still_consumed(control):
    worker = control.control.permissions.issue('observer')
    token = control.control.break_glass_issue('docs/note.md', worker['session_id'], 'maintenance')['token']
    def fail(*args):
        raise OSError('simulated disk failure')
    with control.control.permissions.session(worker['token']):
        with pytest.raises(OSError):
            control.wrap_write_file(fail)('docs/note.md', 'x', break_glass=token)
        with pytest.raises(WriteDenied, match='break-glass'):
            GateProxy(control).write_file('docs/note.md', 'x', break_glass=token)
    with control.control.store.transaction() as db:
        assert control.control.store.get(db, 'break_glass', hashlib.sha256(token.encode()).hexdigest())['used']


def test_goal_refuses_inconsistent_binding_and_preserves_evidence(control):
    from conftest import SPEC, GOOD_TESTS
    cp = control.control
    goal = cp.goal_create('first', ['tests pass'], ['one', 'two'])
    with pytest.raises(ValueError, match='Goal'):
        cp.goal_create('second', ['tests pass'], ['one'])
    for title, done, milestones in [(None, ['yes'], ['one']), ('g', None, ['one']),
                                     ('g', ['yes'], []), ('g', ['yes'], ['']), ('g', [''], ['one'])]:
        with pytest.raises(ValueError):
            cp.goal_create(title, done, milestones)
    with cp.store.transaction() as db:
        row = cp.store.get(db, 'goal', goal['goal_id'])
        row['current_milestone'] = 1
        cp.store.put(db, 'goal', row['goal_id'], row)
    with pytest.raises(PermissionError, match='恢复'):
        cp.assert_running()
    with pytest.raises(ValueError, match='resume'):
        cp.goal_action(goal['goal_id'], 'next')
    with cp.store.transaction() as db:
        row['current_milestone'] = 0
        cp.store.put(db, 'goal', row['goal_id'], row)
    cp.goal_action(goal['goal_id'], 'pause')
    with pytest.raises(ValueError, match='active'):
        cp.goal_action(goal['goal_id'], 'next')
    cp.goal_action(goal['goal_id'], 'resume')
    assert control.advance_stage(1)['success']
    GateProxy(control).write_file('spec.md', SPEC)
    assert control.advance_stage(2)['success']
    GateProxy(control).write_file('test_fib.py', GOOD_TESTS)
    assert control.advance_stage(3)['success']
    before = control.state.snapshot()
    cp.goal_action(goal['goal_id'], 'abandon')
    assert control.state.snapshot() == before


def test_cross_goal_actions_and_invalid_grants_rejected(control):
    cp = control.control
    goal = cp.goal_create('first', ['tests pass'], ['one'])
    with cp.store.transaction() as db:
        foreign = {**goal, 'goal_id': 'foreign'}
        cp.store.put(db, 'goal', 'foreign', foreign)
    with pytest.raises(ValueError, match='owns'):
        cp.goal_action('foreign', 'resume')
    user = cp.permissions.issue('worker')
    for ttl in [0, True, 901, '900']:
        with pytest.raises(ValueError, match='ttl'):
            cp.break_glass_issue('notes.md', user['session_id'], 'reason', ttl=ttl)
    with pytest.raises(ValueError, match='session'):
        cp.break_glass_issue('notes.md', 'missing', 'reason')
    cp.permissions.revoke(user['session_id'])
    with pytest.raises(ValueError, match='session'):
        cp.break_glass_issue('notes.md', user['session_id'], 'reason')


def test_snapshot_tolerates_audit_noise_and_filters_other_goal_cycles(control):
    cp = control.control
    goal = cp.goal_create('first', ['tests pass'], ['one'])
    path = control.gate_dir / control.config.audit_log_name
    with path.open('a', encoding='utf-8') as stream:
        stream.write('broken\n[]\n')
        stream.write(json.dumps({'event': 'stage_advance_defense_rejected',
                                 'goal_id': goal['goal_id'], 'milestone_index': 0,
                                 'evidence': {'defense_checks': [{'line': 'behavior_audit', 'ok': False}]}}) + '\n')
        stream.write(json.dumps({'event': 'foreign', 'goal_id': 'foreign',
                                 'evidence': {'defense_checks': [{'line': 'behavior_audit', 'ok': True}]}}) + '\n')
    snapshot = cp.snapshot()
    assert snapshot['state']['goal_id'] == goal['goal_id']
    assert snapshot['tasks'] == []


def test_failed_diff_review_does_not_write_or_consume_approval(control):
    cp = control.control
    with pytest.raises(ValueError, match='text or bytes'):
        cp.diffs.preview('note.md', {})
    cp.permissions.update({'version': 1, 'review_writes': ['notes.md']})
    with pytest.raises(WriteDenied):
        GateProxy(control).write_file('notes.md', 'same')
    review = cp.snapshot()['diff']['review_id']
    for approved, reason in [('yes', 'why'), (True, '')]:
        with pytest.raises(ValueError):
            cp.diffs.decide(review, approved, reason)
    cp.diffs.decide(review, True, 'approved')
    assert GateProxy(control).write_file('notes.md', 'same')['ok']
    # Deleting the file reproduces the original diff identity; consumed approvals
    # must return to pending rather than authorizing a second identical write.
    (control.workspace / 'notes.md').unlink()
    with pytest.raises(WriteDenied, match='pending'):
        GateProxy(control).write_file('notes.md', 'same')
    assert cp.snapshot()['diff']['status'] == 'pending'


def test_oversized_diff_and_character_class_scope(control):
    cp = control.control
    cp.permissions.update({'version': 1, 'write_paths': {'worker': ['docs/[ab]?.md']}})
    worker = cp.permissions.issue('worker')
    with cp.permissions.session(worker['token']):
        assert GateProxy(control).write_file('docs/a1.md', 'x' * (2 * 1024 * 1024 + 1))['ok']
        with pytest.raises(WriteDenied, match='范围'):
            GateProxy(control).write_file('docs/c1.md', 'no')
    assert cp.snapshot()['diff']['truncated']


def test_control_artifacts_do_not_pollute_write_audit(control):
    from anti_shortcut.write_audit import snapshot_workspace, diff_snapshots
    before = snapshot_workspace(control.workspace)
    control.control.task_capture('internal task')
    control.control.goal_create('internal goal', ['verified'], ['one'])
    control.control.diffs.preview('notes.md', 'preview only')
    from anti_shortcut.control.scheduler import Scheduler
    Scheduler(control, {'version': 1}).execute('goal_health_report', {})
    assert diff_snapshots(before, snapshot_workspace(control.workspace)) == []
    assert control.unauthorized_writes == []


def test_gate_directory_is_rejected_before_plugin_allow_rule(control, monkeypatch):
    import anti_shortcut.skill as module
    called = []
    def allow(*args, **kwargs):
        called.append(args)
        return True, 'plugin would allow'
    monkeypatch.setattr(module, 'evaluate_rules', allow)
    before = (control.gate_dir / 'state.json').read_bytes()
    with pytest.raises(WriteDenied, match='门禁目录'):
        GateProxy(control).write_file('.agent_gate/state.json', '{}')
    with pytest.raises(PermissionError, match='门禁目录'):
        control.wrap_write_file(lambda *_: pytest.fail('raw write reached'))('.agent_gate/diff/latest.json', '{}')
    assert called == []
    assert (control.gate_dir / 'state.json').read_bytes() == before


def test_delegation_cannot_supply_missing_test_evidence(control):
    from conftest import SPEC
    worker = control.control.permissions.issue('worker')
    token = control.control.break_glass_issue('fib.py', worker['session_id'], 'cannot skip')['token']
    with control.control.permissions.session(worker['token']):
        with pytest.raises(WriteDenied, match='测试用例'):
            GateProxy(control).write_file('fib.py', 'def fib(n): return n', break_glass=token)
        GateProxy(control).write_file('spec.md', SPEC)
        assert control.advance_stage(2)['success']
        with pytest.raises(WriteDenied, match='break-glass'):
            GateProxy(control).write_file('fib.py', 'def fib(n): return n', break_glass=token)
        result = control.advance_stage(3)
        assert not result['success'] and control.current_stage == 2
        assert not (control.workspace / 'fib.py').exists()


def test_cli_read_actions_and_goal_recovery_paths(control, monkeypatch, capsys):
    from anti_shortcut.__main__ import main
    token = control.control.permissions.issue('admin', 'human')['token']
    monkeypatch.setenv('CONTROL_TEST_TOKEN', token)
    common = ['--workspace', str(control.workspace), '--token-env', 'CONTROL_TEST_TOKEN']
    for args in [['control', 'snapshot'], ['control', 'task_capture', '--data', '{"title":"cli task"}'],
                 ['task', 'list'], ['goal', 'status']]:
        assert main([*args, *common]) == 0
        assert isinstance(json.loads(capsys.readouterr().out), (list, dict))
    goal = control.control.goal_create('goal', ['tests pass'], ['one'])
    assert main(['goal', 'pause', '--goal-id', goal['goal_id'], *common]) == 0
    capsys.readouterr()
    assert main(['goal', 'resume', '--goal-id', goal['goal_id'], *common]) == 0
    capsys.readouterr()


def test_ui_writes_are_limited_and_concurrent_diff_renders_are_serialized(control):
    import asyncio
    pytest.importorskip('textual')
    from anti_shortcut.control.tui import GateApp
    from textual.widgets import Button, Input
    GateProxy(control).write_file('notes.md', 'before')
    snapshot = control.control.snapshot()
    calls = []
    class Client:
        def events(self, cursor):
            return {'cursor': 1, 'events': []}
        def control_snapshot(self):
            return snapshot
        def control(self, operation, **data):
            calls.append((operation, data))
    async def check():
        app = GateApp(Client())
        async with app.run_test(size=(150, 60)) as pilot:
            await pilot.pause()
            assert {button.id for button in app.query(Button)} == {'approve', 'reject', 'claim'}
            await asyncio.gather(*(app.render_snapshot() for _ in range(3)))
            assert len(app.query('DiffView')) == 1
            app.query_one('#task-id', Input).value = 'missing-task'
            await app.on_button_pressed(Button.Pressed(app.query_one('#claim', Button)))
            assert calls == [], 'Invalid claim should not call the API'
    async def bounded():
        await asyncio.wait_for(check(), timeout=20)
    asyncio.run(bounded())
