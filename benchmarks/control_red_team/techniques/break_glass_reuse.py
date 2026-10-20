from anti_shortcut.proxy import WriteDenied
from ..base import ControlTechnique, IMPL, denied


def attempt(env, mode):
    first, second = env.issue('observer'), env.issue('observer')
    path = 'add.py' if mode == 'stage' else 'docs/note.md'
    grant = env.control.break_glass_issue(path, first['session_id'], 'boundary verification')
    token = grant['token']
    if mode == 'expired':
        env.expire(token, 'break_glass')
    if mode == 'directory':
        with denied(PermissionError, match='门禁'):
            env.control.break_glass_issue('.agent_gate/state.json', first['session_id'], 'no bypass')
    user = second if mode == 'session' else first
    with env.permissions.session(user['token']):
        if mode in {'file', 'session', 'expired', 'directory', 'stage'}:
            target = 'docs/other.md' if mode == 'file' else '.agent_gate/permissions.yaml' if mode == 'directory' else path
            with denied(WriteDenied):
                env.proxy.write_file(target, IMPL if mode == 'stage' else 'blocked', break_glass=token)
            assert not (env.root / path).exists()
        elif mode == 'failure':
            def fail(*_):
                raise OSError('injected disk failure')
            with denied(OSError, match='injected'):
                env.skill.wrap_write_file(fail)(path, 'x', break_glass=token)
        else:
            assert env.proxy.write_file(path, 'first', break_glass=token)['ok']
        if mode in {'reuse', 'failure', 'stage'}:
            with denied(WriteDenied, match='break-glass'):
                env.proxy.write_file(path, 'retry', break_glass=token)
        if mode == 'stage':
            with denied(PermissionError, match='权限拒绝'):
                env.skill.advance_stage(6)
    # A wrong path/session cannot steal another session's valid grant.
    if mode in {'file', 'session'}:
        with env.permissions.session(first['token']):
            assert env.proxy.write_file(path, 'valid owner', break_glass=token)['ok']


class BreakGlassReuse(ControlTechnique):
    name = 'break_glass_reuse'
    cases = {mode: (lambda env, mode=mode: attempt(env, mode)) for mode in
             ['file', 'session', 'expired', 'failure', 'directory', 'stage', 'reuse']}
