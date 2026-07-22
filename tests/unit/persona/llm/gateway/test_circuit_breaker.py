"""CircuitBreaker 状态机单元测试: active → disabled → active 生命周期"""
import time
import pytest
from unittest.mock import patch

from module.persona.llm.circuit_breaker import CircuitBreaker, CircuitBreakerRegistry


class TestStateTransitions:
    def test_initial_state(self):
        cb = CircuitBreaker("p1", "m1")
        assert cb.state == "active"
        assert cb.failure_count == 0
        assert cb.is_available() is True

    def test_record_failure_increments_count(self):
        cb = CircuitBreaker("p1", "m1", failure_threshold=3)
        cb.record_failure()
        assert cb.failure_count == 1
        assert cb.state == "active"

        cb.record_failure()
        assert cb.failure_count == 2
        assert cb.state == "active"

    def test_record_failure_transitions_to_disabled(self):
        cb = CircuitBreaker("p1", "m1", failure_threshold=2)
        with patch.object(cb, '_transition') as mock_transition:
            cb.record_failure()
            cb.record_failure()
            mock_transition.assert_called_once()
            assert "disabled" in str(mock_transition.call_args)

    def test_record_failure_ignored_when_disabled(self):
        cb = CircuitBreaker("p1", "m1", failure_threshold=2)
        cb.record_failure()
        cb.record_failure()  # → disabled
        prev_count = cb.failure_count
        cb.record_failure()
        assert cb.failure_count == prev_count  # disabled 不再递增
        assert cb.state == "disabled"  # stays disabled

    def test_record_failure_ignored_when_dead(self):
        cb = CircuitBreaker("p1", "m1", failure_threshold=1)
        cb.mark_dead("auth error")
        cb.record_failure()
        assert cb.state == "dead"

    def test_record_success_resets_count(self):
        cb = CircuitBreaker("p1", "m1", failure_threshold=3)
        cb.record_failure()
        cb.record_failure()
        assert cb.failure_count == 2
        cb.record_success()
        assert cb.failure_count == 0
        assert cb.state == "active"

    def test_record_success_transitions_disabled_to_active(self):
        cb = CircuitBreaker("p1", "m1", failure_threshold=1)
        cb.record_failure()  # → disabled
        assert cb.state == "disabled"
        cb.record_success()  # → active
        assert cb.state == "active"
        assert cb.failure_count == 0

    def test_record_success_ignored_when_dead(self):
        cb = CircuitBreaker("p1", "m1")
        cb.mark_dead("auth error")
        cb.record_success()
        assert cb.state == "dead"

    def test_mark_dead(self):
        cb = CircuitBreaker("p1", "m1")
        cb.mark_dead("auth error")
        assert cb.state == "dead"

    def test_mark_dead_idempotent(self):
        cb = CircuitBreaker("p1", "m1")
        cb.mark_dead("auth error")
        cb.mark_dead("another error")
        assert cb.state == "dead"

    def test_mark_disabled(self):
        cb = CircuitBreaker("p1", "m1")
        cb.mark_disabled("test disable")
        assert cb.state == "disabled"

    def test_mark_disabled_sets_probe_time(self):
        cb = CircuitBreaker("p1", "m1")
        with patch.object(cb, '_last_probe_time', 0.0):
            cb.mark_disabled("test")
            assert cb._last_probe_time > 0.0

    def test_record_failure_sets_probe_time_when_disabling(self):
        cb = CircuitBreaker("p1", "m1", failure_threshold=1)
        with patch.object(cb, '_last_probe_time', 0.0):
            cb.record_failure()  # → disabled
            assert cb._last_probe_time > 0.0


class TestAvailability:
    def test_active_is_available(self):
        cb = CircuitBreaker("p1", "m1")
        assert cb.is_available() is True

    def test_dead_not_available(self):
        cb = CircuitBreaker("p1", "m1")
        cb.mark_dead("auth error")
        assert cb.is_available() is False

    @patch('time.monotonic', return_value=1000.0)
    def test_disabled_probe_available_after_interval(self, mock_mono):
        cb = CircuitBreaker("p1", "m1", probe_interval_seconds=300)
        # Manually set disabled with old probe time
        cb._state = "disabled"
        cb._last_probe_time = 0.0
        assert cb.is_available() is True  # 0.0 vs 1000.0 > 300s

    def test_disabled_not_available_before_interval(self):
        cb = CircuitBreaker("p1", "m1", probe_interval_seconds=300)
        cb._state = "disabled"
        cb._last_probe_time = time.monotonic()  # just now
        assert cb.is_available() is False

    @patch('time.monotonic', return_value=1000.0)
    def test_should_probe_only_when_disabled(self, mock_mono):
        cb = CircuitBreaker("p1", "m1", probe_interval_seconds=300)
        assert cb.should_probe() is False  # active

        cb._state = "dead"
        assert cb.should_probe() is False  # dead

        cb._state = "disabled"
        cb._last_probe_time = 0.0
        assert cb.should_probe() is True  # disabled, old probe, 1000.0 > 300s

    def test_on_probe_start_updates_time(self):
        cb = CircuitBreaker("p1", "m1")
        cb._state = "disabled"
        cb._last_probe_time = 0.0
        cb.on_probe_start()
        assert cb._last_probe_time > 0.0

    def test_on_probe_failure_keeps_disabled(self):
        cb = CircuitBreaker("p1", "m1")
        cb._state = "disabled"
        cb.on_probe_failure()
        assert cb.state == "disabled"
        assert cb.consecutive_probe_failures == 1


class TestExhaustedState:
    def test_probe_failures_transition_to_exhausted(self):
        cb = CircuitBreaker("p1", "m1")
        cb._state = "disabled"
        for i in range(10):
            cb.on_probe_failure()
        assert cb.state == "exhausted"
        assert cb.consecutive_probe_failures == 10

    def test_exhausted_not_available(self):
        cb = CircuitBreaker("p1", "m1")
        cb._state = "exhausted"
        assert cb.is_available() is False

    def test_exhausted_not_probed(self):
        cb = CircuitBreaker("p1", "m1")
        cb._state = "exhausted"
        cb._last_probe_time = 0.0
        assert cb.should_probe() is False

    def test_reset_probe_exhausted_to_disabled(self):
        cb = CircuitBreaker("p1", "m1")
        cb._state = "exhausted"
        cb._consecutive_probe_failures = 10
        cb.reset_probe()
        assert cb.state == "disabled"
        assert cb.consecutive_probe_failures == 0
        assert cb._last_probe_time > 0.0

    def test_reset_probe_only_from_exhausted(self):
        cb = CircuitBreaker("p1", "m1")
        cb._state = "active"
        cb.reset_probe()
        assert cb.state == "active"  # no-op

    def test_record_success_resets_probe_failures(self):
        cb = CircuitBreaker("p1", "m1")
        cb._state = "disabled"
        for _ in range(5):
            cb.on_probe_failure()
        assert cb.consecutive_probe_failures == 5
        cb.record_success()
        assert cb.consecutive_probe_failures == 0
        assert cb.state == "active"

    def test_exhausted_skipped_by_get_disabled_keys(self):
        reg = CircuitBreakerRegistry()
        cb = reg.get_or_create("p1", "m1")
        cb._state = "exhausted"
        assert reg.get_disabled_keys() == []

    def test_disabled_not_exhausted_before_threshold(self):
        cb = CircuitBreaker("p1", "m1")
        cb._state = "disabled"
        for _ in range(9):
            cb.on_probe_failure()
        assert cb.state == "disabled"
        assert cb.consecutive_probe_failures == 9


class TestRegistry:
    def test_get_or_create_returns_same_instance(self):
        reg = CircuitBreakerRegistry()
        cb1 = reg.get_or_create("p1", "m1")
        cb2 = reg.get_or_create("p1", "m1")
        assert cb1 is cb2

    def test_get_returns_none_for_unknown(self):
        reg = CircuitBreakerRegistry()
        assert reg.get("p1", "unknown") is None

    def test_all_models_disabled(self):
        reg = CircuitBreakerRegistry()
        cb = reg.get_or_create("p1", "m1")
        cb.mark_dead("test")
        assert reg.all_models_disabled([("p1", "m1")]) is True

    def test_all_models_disabled_empty_keys(self):
        reg = CircuitBreakerRegistry()
        assert reg.all_models_disabled([]) is True

    def test_get_disabled_keys(self):
        reg = CircuitBreakerRegistry()
        reg.get_or_create("p1", "m1")  # active
        cb2 = reg.get_or_create("p2", "m2")
        cb2._state = "disabled"
        disabled = reg.get_disabled_keys()
        assert ("p2", "m2") in disabled
        assert ("p1", "m1") not in disabled
