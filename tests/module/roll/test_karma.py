import pytest
from unittest.async_case import IsolatedAsyncioTestCase

from module.roll.karma_manager import KarmaConfig, KarmaState, DEFAULT_WINDOW, DEFAULT_PERCENTAGE


class TestKarmaConfig:
    def test_roundtrip_default(self):
        cfg = KarmaConfig()
        data = cfg.to_dict()
        restored = KarmaConfig.from_dict(data)
        assert cfg.is_enabled == restored.is_enabled
        assert cfg.mode == restored.mode
        assert cfg.engine == restored.engine
        assert cfg.custom_percentage == restored.custom_percentage
        assert cfg.custom_roll_count == restored.custom_roll_count

    def test_roundtrip_custom(self):
        cfg = KarmaConfig(
            is_enabled=True,
            mode="hero",
            engine="advantage",
            custom_percentage=70,
            custom_roll_count=30,
            intro_sent=True,
        )
        data = cfg.to_dict()
        restored = KarmaConfig.from_dict(data)
        assert cfg.is_enabled == restored.is_enabled
        assert cfg.mode == restored.mode
        assert cfg.engine == restored.engine
        assert cfg.custom_percentage == restored.custom_percentage
        assert cfg.custom_roll_count == restored.custom_roll_count
        assert cfg.intro_sent == restored.intro_sent

    def test_from_dict_none(self):
        cfg = KarmaConfig.from_dict(None)
        assert not cfg.is_enabled
        assert cfg.mode == "custom"
        assert cfg.engine == "precise"
        assert cfg.custom_percentage == DEFAULT_PERCENTAGE
        assert cfg.custom_roll_count == DEFAULT_WINDOW

    def test_from_dict_partial(self):
        data = {"mode": "hero", "custom_percentage": 80}
        cfg = KarmaConfig.from_dict(data)
        assert cfg.mode == "hero"
        assert cfg.custom_percentage == 80
        assert cfg.engine == "advantage"  # Default engine is "advantage"
        assert cfg.custom_roll_count == DEFAULT_WINDOW

    def test_from_group_config_none(self):
        cfg = KarmaConfig.from_group_config(None)
        assert not cfg.is_enabled
        assert cfg.mode == "custom"

    def test_from_group_config_with_karma(self):
        group_data = {"karma": {"is_enabled": True, "mode": "hero", "engine": "advantage"}}
        cfg = KarmaConfig.from_group_config(group_data)
        assert cfg.is_enabled
        assert cfg.mode == "hero"
        assert cfg.engine == "advantage"

    def test_from_group_config_without_karma(self):
        group_data = {"other_setting": "value"}
        cfg = KarmaConfig.from_group_config(group_data)
        assert not cfg.is_enabled
        assert cfg.mode == "custom"


class TestKarmaState:
    def test_append_and_average(self):
        state = KarmaState()
        for v in [10.0, 20.0, 30.0, 40.0, 50.0]:
            state.append(v)
        assert state.average() == 30.0

    def test_window_overflow(self):
        state = KarmaState()
        state.resize(3)
        state.append(10.0)
        state.append(20.0)
        state.append(30.0)
        state.append(40.0)
        assert list(state.history) == [20.0, 30.0, 40.0]

    def test_empty_average(self):
        state = KarmaState()
        assert state.average() == 50.0

    def test_resize(self):
        state = KarmaState()
        state.append(10.0)
        state.append(20.0)
        state.append(30.0)
        state.resize(2)
        assert state.window == 2
        assert list(state.history) == [20.0, 30.0]

    def test_tail(self):
        state = KarmaState()
        for v in [10.0, 20.0, 30.0, 40.0, 50.0]:
            state.append(v)
        assert state.tail(3) == [30.0, 40.0, 50.0]
        assert state.tail(0) == []
        assert state.tail(10) == [10.0, 20.0, 30.0, 40.0, 50.0]


@pytest.mark.unit
class TestKarmaEngines:
    """Fast unit tests using mocked bot."""

    @pytest.fixture
    def mock_bot(self):
        from unittest.mock import MagicMock
        bot = MagicMock()
        bot.db = MagicMock()
        bot.db.group_config = MagicMock()
        bot.db.group_config.get = MagicMock(return_value=None)
        return bot

    @pytest.fixture
    def manager(self, mock_bot):
        from module.roll.karma_manager import KarmaDiceManager
        return KarmaDiceManager(mock_bot)

    def test_standard_is_uniform(self, manager):
        import random
        random.seed(42)
        values = [manager.generate_value("g1", "u1", 100) for _ in range(1000)]
        avg = sum(values) / len(values)
        assert avg > 40
        assert avg < 60

    def test_grim_mode_skews_low(self, manager):
        import random
        random.seed(42)
        from module.roll.karma_manager import KarmaConfig
        cfg = KarmaConfig(is_enabled=True, mode="grim")
        manager.set_runtime("g1", cfg)

        values = [manager.generate_value("g1", "u1", 100) for _ in range(1000)]
        avg = sum(values) / len(values)
        assert avg < 55

    def test_stable_mode_lower_variance(self, manager):
        import random
        random.seed(42)
        from module.roll.karma_manager import KarmaConfig
        import statistics

        cfg_standard = KarmaConfig(is_enabled=True, mode="custom")
        cfg_stable = KarmaConfig(is_enabled=True, mode="stable")
        manager.set_runtime("g1", cfg_standard)
        manager.set_runtime("g2", cfg_stable)

        values_standard = [manager.generate_value("g1", "u1", 100) for _ in range(500)]
        values_stable = [manager.generate_value("g2", "u2", 100) for _ in range(500)]

        var_standard = statistics.variance(values_standard)
        var_stable = statistics.variance(values_stable)
        assert var_stable < var_standard


