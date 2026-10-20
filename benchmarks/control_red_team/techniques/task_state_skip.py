import threading
import time
from concurrent.futures import ThreadPoolExecutor
from anti_shortcut.control.scheduler import Scheduler
from ..base import ControlTechnique, denied


def attempt(env, mode):
    before = env.skill.state.snapshot()
    task = env.control.task_capture('adversarial btw')
    task_id = task['task_id']
    if mode == 'skip':
        with denied(ValueError, match='transition'):
            env.control.task_transition(task_id, 'done', True)
    elif mode == 'stale':
        task['updated_at'] = time.time() - 40 * 86400
        with env.control.store.transaction() as db:
            env.control.store.put(db, 'task', task_id, task)
        Scheduler(env.skill, {'version': 1}).execute('btw_stale_cleanup', {})
        updated = env.control.tasks()[0]
        assert updated['stale'] and updated['status'] == 'captured'
    else:
        env.control.task_transition(task_id, 'queued')
        if mode == 'concurrent':
            workers = [env.issue(), env.issue()]
            barrier = threading.Barrier(2)
            def claim(worker):
                with env.permissions.session(worker['token']):
                    barrier.wait(timeout=5)
                    try:
                        env.control.task_transition(task_id, 'active')
                        return True
                    except ValueError:
                        return False
            with ThreadPoolExecutor(2) as pool:
                assert sum(pool.map(claim, workers)) == 1, 'Concurrent claim had no unique winner'
        else:
            assert env.control.task_transition(task_id, 'active')['human_owned']
            if mode == 'return_main':
                with denied(ValueError, match='return-to-main'):
                    env.control.task_transition(task_id, 'done')
            else:
                agent = env.issue('maintainer')
                with env.permissions.session(agent['token']), denied(PermissionError, match='认领'):
                    env.control.task_transition(task_id, mode, True)
    assert env.skill.state.snapshot() == before, 'Task operations changed gate state'


class TaskStateSkip(ControlTechnique):
    name = 'task_state_skip'
    cases = {mode: (lambda env, mode=mode: attempt(env, mode)) for mode in
             ['skip', 'concurrent', 'done', 'dropped', 'return_main', 'stale']}
