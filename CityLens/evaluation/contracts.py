"""Contract for a future mock or isolated-lab adapter. No concrete adapter is installed."""
from typing import Protocol


class ScenarioAdapter(Protocol):
    def reset(self, setup: dict) -> None:
        """Create clean per-case state, independent from stable and previous cases."""
        ...

    def execute_step(self, step: dict) -> dict:
        """Run one step and return a normalized observation with captured evidence."""
        ...

    def close(self) -> None:
        """Cancel pending work and release temporary state, even after an error."""
        ...


RECORD_VERSION = 1
EXECUTION_STATES = {'executed', 'blocked', 'not_run'}
VERDICTS = {'pass', 'fail', 'needs_review', 'not_evaluable', 'blocked', 'not_run', 'deferred'}
