"""Local adversarial probes for the optional control-plane trust boundaries."""

# Initialize shared red-team types before importing probe modules in either order.
from benchmarks.red_team import techniques as _shared_techniques  # noqa: F401
