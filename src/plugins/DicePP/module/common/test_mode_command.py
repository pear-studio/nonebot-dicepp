import unittest
import pytest

import sys
from pathlib import Path
dicepp_path = Path(__file__).parent.parent
if str(dicepp_path) not in sys.path:
    sys.path.insert(0, str(dicepp_path))


@pytest.mark.unit
class TestModeCommand(unittest.TestCase):
    @pytest.fixture(autouse=True)
    def setup_bot(self):
        from core.bot import Bot
        from core.config import ConfigItem, CFG_MASTER

        self.bot = Bot("test_mode_bot")
        self.bot.cfg_helper.all_configs[CFG_MASTER] = ConfigItem(CFG_MASTER, "test_master")
        self.bot.cfg_helper.save_config()
        self.bot.delay_init_debug()

        yield

        self.bot.shutdown_debug()
        import os
        test_path = self.bot.data_path
        if os.path.exists(test_path):
            for root, dirs, files in os.walk(test_path, topdown=False):
                for name in files:
                    os.remove(os.path.join(root, name))
                for name in dirs:
                    os.rmdir(os.path.join(root, name))
            os.rmdir(test_path)

    def test_mode_switch_to_coc(self):
        from core.communication import MessageMetaData, MessageSender
        from module.common.mode_command import ModeCommand

        meta = MessageMetaData(".mode COC7", ".mode COC7", MessageSender("user1", "User"), "test_group", False)
        cmds = self.bot.process_message(".mode COC7", meta)
        result_str = "\n".join([str(c) for c in cmds])
        self.assertIn("COC7", result_str)

    def test_mode_switch_to_dnd(self):
        from core.communication import MessageMetaData, MessageSender

        meta = MessageMetaData(".mode DND5E2024", ".mode DND5E2024", MessageSender("user1", "User"), "test_group", False)
        cmds = self.bot.process_message(".mode DND5E2024", meta)
        result_str = "\n".join([str(c) for c in cmds])
        self.assertIn("DND5E2024", result_str)

    def test_mode_invalid(self):
        from core.communication import MessageMetaData, MessageSender

        meta = MessageMetaData(".mode INVALID_MODE", ".mode INVALID_MODE", MessageSender("user1", "User"), "test_group", False)
        cmds = self.bot.process_message(".mode INVALID_MODE", meta)
        result_str = "\n".join([str(c) for c in cmds])
        self.assertTrue(len(result_str) > 0)

    def test_mode_list(self):
        from core.communication import MessageMetaData, MessageSender

        meta = MessageMetaData(".mode", ".mode", MessageSender("user1", "User"), "test_group", False)
        cmds = self.bot.process_message(".mode", meta)
        result_str = "\n".join([str(c) for c in cmds])
        self.assertTrue(len(result_str) > 0)
