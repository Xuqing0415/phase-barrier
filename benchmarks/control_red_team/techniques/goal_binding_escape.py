from anti_shortcut.proxy import WriteDenied, ExecDenied
from ..base import ControlTechnique, denied


def attempt(env, mode):
    if mode.startswith('stage_'):
        env.to_stage(int(mode[-1]))
        before = env.skill.state.snapshot()
        with denied(ValueError, match='阶段'):
            env.control.goal_create('overwrite work', ['must pass'], ['milestone'])
        assert env.skill.state.snapshot() == before
        return
    goal = env.control.goal_create('boundary goal', ['addition tests pass'], ['one', 'two'])
    goal_id = goal['goal_id']
    if mode == 'second':
        with denied(ValueError, match='Goal'):
            env.control.goal_create('second', ['tests'], ['other'])
    elif mode == 'unfinished':
        with denied(ValueError, match='门禁'):
            env.control.goal_action(goal_id, 'next')
    elif mode == 'paused':
        env.control.goal_action(goal_id, 'pause')
        before = env.skill.state.snapshot()
        with denied(WriteDenied, match='暂停'):
            env.proxy.write_file('notes.md', 'no')
        with denied(ExecDenied, match='暂停'):
            env.proxy.execute_command('echo no')
        with denied(PermissionError, match='暂停'):
            env.skill.advance_stage(1)
        assert env.skill.state.snapshot() == before
    elif mode == 'abandon':
        env.to_stage(3)
        before = env.skill.state.snapshot()
        manifest = env.skill.evidence_manifest.entries()
        env.control.goal_action(goal_id, 'abandon')
        assert env.skill.state.snapshot() == before
        assert env.skill.evidence_manifest.entries() == manifest
        assert (env.root / 'spec.md').exists() and (env.root / 'test_add.py').exists()
    elif mode in {'last_evidence', 'tamper'}:
        env.to_stage(6)
        if mode == 'tamper':
            (env.root / 'spec.md').write_text('tampered', encoding='utf-8')
            with denied(ValueError, match='证据'):
                env.control.goal_action(goal_id, 'next')
            return
        env.control.goal_action(goal_id, 'next')
        assert env.skill.current_stage == 0
        env.to_stage(6)
        final = env.control.goal_action(goal_id, 'next')
        assert final['status'] == 'completed' and len(final['evidence']) == 2
        assert [e['milestone_index'] for e in final['evidence']] == [0, 1]
        assert all(e['state']['current_stage'] == 6 and e['manifest'] for e in final['evidence'])
        env.evidence['milestones_with_evidence'] = len(final['evidence'])


class GoalBindingEscape(ControlTechnique):
    name = 'goal_binding_escape'
    cases = {mode: (lambda env, mode=mode: attempt(env, mode)) for mode in
             ['stage_2', 'stage_3', 'stage_4', 'stage_5', 'unfinished', 'last_evidence',
              'abandon', 'second', 'paused', 'tamper']}
