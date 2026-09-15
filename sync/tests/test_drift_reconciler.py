"""Drift reconciler: tables it gives up on stay given up across restarts."""
from sync.drift_reconciler import EPOCH, DriftReconcilerMixin

TABLE = "public.calSchedules_calibrationschedule"


class FakeState:
    def __init__(self):
        self.values = {}

    def get(self, key, default=None):
        return self.values.get(key, default)

    def set(self, key, value):
        self.values[key] = value


class FakeChecker:
    """HQ always reports the same structural deficit for TABLE."""

    def compute_local_counts(self):
        return {TABLE: 3791}

    def compare_with_hq(self, local_counts):
        return {"out_of_sync": [{"table": TABLE, "hq_count": 3700, "client_count": 3791}]}


class Agent(DriftReconcilerMixin):
    DRIFT_REWIND_COOLDOWN_S = 0

    def __init__(self, state):
        self.state = state
        self.data_checker = FakeChecker()
        self._drift_init()


def test_futile_table_is_given_up_and_stays_given_up_after_restart():
    state = FakeState()
    agent = Agent(state)

    rewinds = [agent.reconcile_drift_once() for _ in range(5)]
    assert sum(1 for r in rewinds if TABLE in r) == Agent.DRIFT_MAX_FUTILE_REWINDS
    assert TABLE in agent._drift_given_up

    # A fresh agent (app restart) must not rewind it again.
    state.set(f"last_upload_time:{TABLE}", "2026-09-01T00:00:00+00:00")
    restarted = Agent(state)
    assert restarted.reconcile_drift_once() == {}
    assert state.get(f"last_upload_time:{TABLE}") != EPOCH


def test_reset_env_clears_given_up_tables(monkeypatch):
    state = FakeState()
    state.set(Agent.DRIFT_GIVEN_UP_KEY, TABLE)
    monkeypatch.setenv("DRIFT_RESET_GIVEN_UP", "1")
    assert Agent(state)._drift_given_up == set()
    assert state.get(Agent.DRIFT_GIVEN_UP_KEY) == ""
