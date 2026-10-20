import json
import threading
import urllib.error
import urllib.request

from anti_shortcut.sidecar import make_server
from ..base import ControlTechnique


def attempt(env, mode):
    worker = env.issue('worker')
    observer = env.issue('observer', 'human')
    maintainer_agent = env.issue('maintainer')
    selected = observer
    operation, data = 'diff_decide', {'review_id': 'unknown', 'approved': True, 'reason': 'bypass UI'}
    if mode == 'worker_goal':
        selected = worker
        operation, data = 'goal_create', {'title': 'no', 'definition_of_done': ['check'], 'milestones': ['one']}
    elif mode == 'agent_review':
        selected = maintainer_agent
    elif mode == 'expired':
        env.expire(observer['token'])
    elif mode == 'revoked':
        env.permissions.revoke(observer['session_id'])
    server = make_server(env.sidecar, host='127.0.0.1', port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    url = f'http://127.0.0.1:{server.server_address[1]}'
    paths = ['/api/control', '/api/events', '/api/state', '/api/audit', '/api/evidence']
    try:
        # A real observer read proves connectivity and authentication are working.
        request = urllib.request.Request(url + '/api/state', headers={'Authorization': 'Bearer ' + worker['token']})
        with opener.open(request, timeout=5) as response:
            assert response.status == 200
        for path, body in [(p, None) for p in paths] + [('/api/control', {'operation': operation, 'data': data})]:
            if mode in {'observer_review', 'worker_goal', 'agent_review'} and body is None:
                continue
            headers = {'Content-Type': 'application/json'}
            if mode != 'no_token':
                headers['Authorization'] = 'Bearer ' + selected['token']
            request = urllib.request.Request(url + path, data=json.dumps(body).encode() if body else None, headers=headers)
            try:
                with opener.open(request, timeout=5) as response:
                    raise AssertionError(f'Unauthorized direct API call returned {response.status}: {path}')
            except urllib.error.HTTPError as exc:
                assert exc.code == 403, f'Expected permission denial, got {exc.code}'
                assert json.load(exc)['denial'] == 'permission'
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
    env.evidence['transport'] = 'real loopback HTTP; no UI involved'


class ControlBypass(ControlTechnique):
    name = 'control_bypass'
    cases = {mode: (lambda env, mode=mode: attempt(env, mode)) for mode in
             ['no_token', 'expired', 'revoked', 'observer_review', 'worker_goal', 'agent_review']}
