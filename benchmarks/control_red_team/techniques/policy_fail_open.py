from anti_shortcut.control.permissions import ROLES
from anti_shortcut.proxy import WriteDenied
from ..base import ControlTechnique, denied


def attempt(env, mode):
    worker = env.issue()
    if mode in {'malformed', 'missing', 'invalid_scope'}:
        if mode == 'missing':
            env.permissions.path.unlink()
        else:
            env.permissions.path.write_text('roles: [' if mode == 'malformed' else
                                            'version: 1\nreview_writes: ["../*"]\n', encoding='utf-8')
        with env.permissions.session(worker['token']):
            for operation in ROLES['admin']:
                with denied(PermissionError, match='策略'):
                    env.permissions.require(operation, 'docs/note.md')
    elif mode == 'empty_role':
        env.permissions.update({'version': 1, 'roles': {'worker': []}})
        with env.permissions.session(worker['token']):
            for operation in ROLES['worker']:
                with denied(PermissionError, match='权限拒绝'):
                    env.permissions.require(operation, 'docs/note.md')
    elif mode == 'scope_typo':
        env.permissions.update({'version': 1, 'write_paths': {'worker': ['sre/*']}})
        with env.permissions.session(worker['token']), denied(WriteDenied, match='范围'):
            env.proxy.write_file('src/notes.md', 'no')
    elif mode == 'rate':
        env.permissions.update({'version': 1, 'rate_limits': {'write_file': 1}})
        with env.permissions.session(worker['token']):
            env.proxy.write_file('one.md', 'allowed')
            with denied(WriteDenied, match='速率'):
                env.proxy.write_file('two.md', 'blocked')
        assert not (env.root / 'two.md').exists()
    elif mode == 'delegate':
        grant = env.control.break_glass_issue('note.md', worker['session_id'], 'test fail-closed')
        env.permissions.path.write_text('roles: [', encoding='utf-8')
        with env.permissions.session(worker['token']), denied(WriteDenied, match='策略'):
            env.proxy.write_file('note.md', 'no', break_glass=grant['token'])


class PolicyFailOpen(ControlTechnique):
    name = 'policy_fail_open'
    cases = {mode: (lambda env, mode=mode: attempt(env, mode)) for mode in
             ['malformed', 'missing', 'empty_role', 'invalid_scope', 'scope_typo', 'rate', 'delegate']}
