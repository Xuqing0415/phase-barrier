from .break_glass_reuse import BreakGlassReuse
from .control_bypass import ControlBypass
from .goal_binding_escape import GoalBindingEscape
from .policy_fail_open import PolicyFailOpen
from .task_state_skip import TaskStateSkip

CONTROL_TECHNIQUES = (ControlBypass(), TaskStateSkip(), GoalBindingEscape(),
                      BreakGlassReuse(), PolicyFailOpen())
