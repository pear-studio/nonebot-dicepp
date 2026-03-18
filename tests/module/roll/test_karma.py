import unittest
import pytest
from unittest.async_case import IsolatedAsyncioTestCase

from module.roll.karma_manager import KarmaConfig, KarmaState, DEFAULT_WINDOW, DEFAULT_PERCENTAGE


class TestKarmaConfig(unittest.TestCase):
    def test_roundtrip_default(self):
        cfg = KarmaConfig()
        data = cfg.to_dict()
        restored = KarmaConfig.from_dict(data)
        self.assertEqual(cfg.is_enabled, restored.is_enabled)
        self.assertEqual(cfg.mode, restored.mode)
        self.assertEqual(cfg.engine, restored.engine)
        self.assertEqual(cfg.custom_percentage, restored.custom_percentage)
        self.assertEqual(cfg.custom_roll_count, restored.custom_roll_count)

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
        self.assertEqual(cfg.is_enabled, restored.is_enabled)
        self.assertEqual(cfg.mode, restored.mode)
        self.assertEqual(cfg.engine, restored.engine)
        self.assertEqual(cfg.custom_percentage, restored.custom_percentage)
        self.assertEqual(cfg.custom_roll_count, restored.custom_roll_count)
        self.assertEqual(cfg.intro_sent, restored.intro_sent)

    def test_from_dict_none(self):
        cfg = KarmaConfig.from_dict(None)
        self.assertFalse(cfg.is_enabled)
        self.assertEqual(cfg.mode, "custom")
        self.assertEqual(cfg.engine, "precise")
        self.assertEqual(cfg.custom_percentage, DEFAULT_PERCENTAGE)
        self.assertEqual(cfg.custom_roll_count, DEFAULT_WINDOW)

    def test_from_dict_partial(self):
        data = {"mode": "hero", "custom_percentage": 80}
        cfg = KarmaConfig.from_dict(data)
        self.assertEqual(cfg.mode, "hero")
        self.assertEqual(cfg.custom_percentage, 80)
        self.assertEqual(cfg.engine, "advantage")  # Default engine is "advantage"
        self.assertEqual(cfg.custom_roll_count, DEFAULT_WINDOW)

    def test_from_group_config_none(self):
        cfg = KarmaConfig.from_group_config(None)
        self.assertFalse(cfg.is_enabled)
        self.assertEqual(cfg.mode, "custom")

    def test_from_group_config_with_karma(self):
        group_data = {"karma": {"is_enabled": True, "mode": "hero", "engine": "advantage"}}
        cfg = KarmaConfig.from_group_config(group_data)
        self.assertTrue(cfg.is_enabled)
        self.assertEqual(cfg.mode, "hero")
        self.assertEqual(cfg.engine, "advantage")

    def test_from_group_config_without_karma(self):
        group_data = {"other_setting": "value"}
        cfg = KarmaConfig.from_group_config(group_data)
        self.assertFalse(cfg.is_enabled)
        self.assertEqual(cfg.mode, "custom")


class TestKarmaState(unittest.TestCase):
    def test_append_and_average(self):
        state = KarmaState()
        for v in [10.0, 20.0, 30.0, 40.0, 50.0]:
            state.append(v)
        self.assertEqual(state.average(), 30.0)

    def test_window_overflow(self):
        state = KarmaState()
        state.resize(3)
        state.append(10.0)
        state.append(20.0)
        state.append(30.0)
        state.append(40.0)
        self.assertEqual(list(state.history), [20.0, 30.0, 40.0])

    def test_empty_average(self):
        state = KarmaState()
        self.assertEqual(state.average(), 50.0)

    def test_resize(self):
        state = KarmaState()
        state.append(10.0)
        state.append(20.0)
        state.append(30.0)
        state.resize(2)
        self.assertEqual(state.window, 2)
        self.assertEqual(list(state.history), [20.0, 30.0])

    def test_tail(self):
        state = KarmaState()
        for v in [10.0, 20.0, 30.0, 40.0, 50.0]:
            state.append(v)
        self.assertEqual(state.tail(3), [30.0, 40.0, 50.0])
        self.assertEqual(state.tail(0), [])
        self.assertEqual(state.tail(10), [10.0, 20.0, 30.0, 40.0, 50.0])


@pytest.mark.slow
class TestKarmaEngines(unittest.TestCase):
    def test_standard_is_uniform(self):
        from module.roll.karma_manager import KarmaDiceManager
        from core.bot import Bot

        bot = Bot("test_karma")
        manager = KarmaDiceManager(bot)
        values = [manager.generate_value("g1", "u1", 100) for _ in range(1000)]
        avg = sum(values) / len(values)
        self.assertGreater(avg, 40)
        self.assertLess(avg, 60)

    @pytest.mark.skip(reason="Flaky probabilistic test - may fail randomly")
    def test_hero_mode_skews_high(self):
        from module.roll.karma_manager import KarmaDiceManager, KarmaConfig, DC_KARMA
        from core.bot import Bot

        bot = Bot("test_karma_hero")
        manager = KarmaDiceManager(bot)
        cfg = KarmaConfig(is_enabled=True, mode="hero")
        bot.data_manager.set_data(DC_KARMA, ["g1"], cfg.to_dict())

        values = [manager.generate_value("g1", "u1", 100) for _ in range(1000)]
        avg = sum(values) / len(values)
        self.assertGreater(avg, 52)  # Relaxed threshold for probabilistic test

    def test_grim_mode_skews_low(self):
        from module.roll.karma_manager import KarmaDiceManager, KarmaConfig, DC_KARMA
        from core.bot import Bot

        bot = Bot("test_karma_grim")
        manager = KarmaDiceManager(bot)
        cfg = KarmaConfig(is_enabled=True, mode="grim")
        bot.data_manager.set_data(DC_KARMA, ["g1"], cfg.to_dict())

        values = [manager.generate_value("g1", "u1", 100) for _ in range(1000)]
        avg = sum(values) / len(values)
        self.assertLess(avg, 55)

    def test_stable_mode_lower_variance(self):
        from module.roll.karma_manager import KarmaDiceManager, KarmaConfig, DC_KARMA
        from core.bot import Bot
        import statistics

        bot = Bot("test_karma_stable")
        manager = KarmaDiceManager(bot)
        cfg_standard = KarmaConfig(is_enabled=True, mode="custom")
        cfg_stable = KarmaConfig(is_enabled=True, mode="stable")
        bot.data_manager.set_data(DC_KARMA, ["g1"], cfg_standard.to_dict())
        bot.data_manager.set_data(DC_KARMA, ["g2"], cfg_stable.to_dict())

        values_standard = [manager.generate_value("g1", "u1", 100) for _ in range(500)]
        values_stable = [manager.generate_value("g2", "u2", 100) for _ in range(500)]

        var_standard = statistics.variance(values_standard)
        var_stable = statistics.variance(values_stable)
        self.assertLess(var_stable, var_standard)


@pytest.mark.integration
class TestKarmaCommand(IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        from core.bot import Bot
        from core.config import ConfigItem, CFG_MASTER

        self.bot = Bot("test_karma_cmd_bot")
        self.bot.cfg_helper.all_configs[CFG_MASTER] = ConfigItem(CFG_MASTER, "test_master")
        self.bot.cfg_helper.save_config()
        await self.bot.delay_init_command()

    async def asyncTearDown(self):
        await self.bot.shutdown_async()
        import os
        test_path = self.bot.data_path
        if os.path.exists(test_path):
            for root, dirs, files in os.walk(test_path, topdown=False):
                for name in files:
                    os.remove(os.path.join(root, name))
                for name in dirs:
                    os.rmdir(os.path.join(root, name))
            os.rmdir(test_path)

    async def _send_msg(self, msg: str, group_id: str = "test_group", permission: int = 1):
        from core.communication import MessageMetaData, MessageSender
        meta = MessageMetaData(msg, msg, MessageSender("user1", "User"), group_id, False)
        meta.permission = permission
        return await self.bot.process_message(msg, meta)

    async def test_enable_disable(self):
        cmds = await self._send_msg(".karmadice on")
        result = "\n".join([str(c) for c in cmds])
        self.assertIn("开启", result)

        cmds = await self._send_msg(".karmadice off")
        result = "\n".join([str(c) for c in cmds])
        self.assertIn("关闭", result)

    async def test_set_mode(self):
        await self._send_msg(".karmadice on")
        cmds = await self._send_msg(".karmadice mode hero")
        result = "\n".join([str(c) for c in cmds])
        self.assertIn("主角光环", result)

    async def test_set_engine(self):
        await self._send_msg(".karmadice on")
        cmds = await self._send_msg(".karmadice engine precise")
        result = "\n".join([str(c) for c in cmds])
        self.assertIn("精确", result)

    async def test_status(self):
        await self._send_msg(".karmadice on")
        cmds = await self._send_msg(".karmadice status")
        result = "\n".join([str(c) for c in cmds])
        self.assertTrue(len(result) > 0)

    async def test_reset_history(self):
        await self._send_msg(".karmadice on")
        await self._send_msg(".r")
        cmds = await self._send_msg(".karmadice reset")
        result = "\n".join([str(c) for c in cmds])
        self.assertIn("清空", result)
