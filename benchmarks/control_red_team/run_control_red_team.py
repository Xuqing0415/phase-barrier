"""Control-only entry point: unwaived escapes and inconclusive probes always fail."""
import sys
from pathlib import Path

if __package__ in (None, ''):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from benchmarks.red_team.run_red_team import main as run_red_team

NAMES = 'control_bypass,task_state_skip,goal_binding_escape,break_glass_reuse,policy_fail_open'


def main(argv=None):
    return run_red_team(['--techniques', NAMES, '--fail-on-escape', '--known-residual',
                         str(Path(__file__).with_name('known_residual.yaml')),
                         *(sys.argv[1:] if argv is None else argv)])


if __name__ == '__main__':
    raise SystemExit(main())
