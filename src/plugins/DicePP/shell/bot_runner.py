"""Bot 运行包装器 - 管理 Bot 实例、捕获输出、控制骰子"""

import asyncio
from pathlib import Path
from typing import List, Optional, Dict, Any

from ..core.bot import Bot
from ..core.communication import MessageMetaData, MessageSender, GroupInfo, GroupMemberInfo
from ..utils.logger import logger
from ..adapter import ClientProxy
from ..core.command import BotCommandBase, BotSendMsgCommand
from ..utils.sequence_runtime import SequenceRuntime
# 注意：必须使用裸绝对导入 `module.roll.karma_runtime`，
# 因为 Bot 内部大量使用该路径导入此模块。
# 若使用相对导入 (`..module.roll.karma_runtime`)，会导致
# `sys.modules` 中出现两个副本，ContextVar 的读写将分离，
# 从而使 `--dice` 序列控制完全失效。
from module.roll.karma_runtime import set_runtime, reset_runtime


class CaptureProxy(ClientProxy):
    """捕获 Bot 输出，而不是真的发送到 QQ"""

    def __init__(self):
        super().__init__()
        self.commands: List[BotCommandBase] = []

    async def get_group_list(self) -> List[GroupInfo]:
        """返回空群组列表（shell 模式下无实际群组）"""
        return []

    async def get_group_info(self, group_id: str) -> GroupInfo:
        """返回虚拟群组信息"""
        return GroupInfo(group_id=group_id or "test_group")

    async def get_group_member_list(self, group_id: str) -> List[GroupMemberInfo]:
        """返回空成员列表"""
        return []

    async def get_group_member_info(self, group_id: str, user_id: str) -> GroupMemberInfo:
        """返回虚拟成员信息"""
        return GroupMemberInfo(group_id=group_id or "test_group", user_id=user_id)

    async def process_bot_command(self, command: BotCommandBase):
        """捕获单个命令"""
        self.commands.append(command)

    async def process_bot_command_list(self, command_list: List[BotCommandBase]):
        """捕获命令列表"""
        self.commands.extend(command_list)

    def get_display_text(self) -> str:
        """将捕获的命令转换为可读的文本输出"""
        lines = []
        for cmd in self.commands:
            if isinstance(cmd, BotSendMsgCommand):
                lines.append(cmd.msg)
        return "\n".join(lines) if lines else "(no output)"

    def clear(self):
        """清空捕获的命令"""
        self.commands.clear()

    def to_dict_list(self) -> List[Dict[str, Any]]:
        """将命令转换为字典列表（用于 JSON 输出）"""
        result = []
        for cmd in self.commands:
            if isinstance(cmd, BotSendMsgCommand):
                targets = []
                for t in cmd.targets:
                    target_info = {"type": t.__class__.__name__}
                    # 提取常见属性
                    if hasattr(t, "group_id"):
                        target_info["group_id"] = t.group_id
                    if hasattr(t, "user_id"):
                        target_info["user_id"] = t.user_id
                    targets.append(target_info)

                result.append({
                    "type": "send_msg",
                    "msg": cmd.msg,
                    "targets": targets,
                })
            else:
                result.append({
                    "type": cmd.__class__.__name__,
                })
        return result


class BotRunner:
    """管理单个 Bot 实例的生命周期和交互"""

    TODO_MAX_WAIT_ITERATIONS = 30
    TODO_POLL_INTERVAL = 0.5

    def __init__(self, session_dir: Path):
        self.session_dir = session_dir
        self.bot: Optional[Bot] = None
        self.proxy = CaptureProxy()
        self._started = False

    async def start(self) -> None:
        """启动 Bot 实例"""
        if self._started:
            return

        # 设置环境变量，让 Bot 使用 session_dir 作为数据目录
        import os
        original_data_dir = os.environ.get("DICEPP_APP_DIR")
        os.environ["DICEPP_APP_DIR"] = str(self.session_dir)

        try:
            # 创建 Bot 实例
            account = f"shell_{self.session_dir.name}"
            self.bot = Bot(account=account, no_tick=True)

            # 配置
            self.bot.config.master = ["shell_master"]
            self.bot.set_client_proxy(self.proxy)

            # 初始化
            await self.bot.delay_init_command()

            # no_tick=True 时 tick_loop 不运行，需要手动处理待办任务（如 persona 异步初始化）
            if self.bot._no_tick and self.bot.scheduler.pending:
                completed = False
                for _ in range(BotRunner.TODO_MAX_WAIT_ITERATIONS):  # 最多等待 30 秒
                    await self.bot.scheduler.process(BotRunner.TODO_POLL_INTERVAL)
                    if not self.bot.scheduler.pending:
                        completed = True
                        break
                    await asyncio.sleep(BotRunner.TODO_POLL_INTERVAL)
                if completed:
                    logger.info("pending tasks 处理完成")
                else:
                    logger.warning("pending tasks 等待超时（30s），仍有未完成任务")

            self._started = True
        finally:
            # 恢复环境变量
            if original_data_dir is not None:
                os.environ["DICEPP_APP_DIR"] = original_data_dir
            elif "DICEPP_APP_DIR" in os.environ:
                del os.environ["DICEPP_APP_DIR"]

    async def stop(self) -> None:
        """停止 Bot 实例"""
        if self.bot and self._started:
            await self.bot.shutdown_async()
            self.bot = None
            self._started = False

    async def warp(
        self,
        days: int,
        start: str | None = None,
        dry_run: bool = False,
    ) -> dict:
        """推进模拟时间，驱动角色生活模拟运行指定天数。

        Args:
            days: 模拟天数
            start: 起始时间（ISO 格式），默认当前真实时间
            dry_run: 仅预估成本，不实际执行

        Returns:
            包含执行结果的字典
        """
        import datetime as dt
        from utils.time import SteppedClock, set_clock, get_clock, WallClock
        from utils.logger import logger

        if not self._started or not self.bot:
            raise RuntimeError("Bot not started. Call start() first.")

        # 获取 PersonaCommand → PersonaApp → LifeSimulator（与 dicebot.py 一致的 isinstance 查找）
        from module.persona.command import PersonaCommand as PCmd
        persona_cmd = next(
            (cmd for cmd in self.bot.command_dict.values() if isinstance(cmd, PCmd)),
            None,
        )
        if persona_cmd is None or persona_cmd.app is None:
            raise RuntimeError(
                "Persona 模块未初始化。请确保 persona 配置已启用，"
                "并在 warp 前至少发送一条消息触发初始化。"
            )

        app = persona_cmd.app
        life_sim = app.life
        char_life = life_sim.character_life

        if char_life is None:
            raise RuntimeError("CharacterLife 未初始化")

        config = char_life.config
        character = char_life.character

        # 计算槽位信息
        daily_events_count = getattr(
            character.extensions, "daily_events_count", 3
        )
        chain_max_depth = config.chain_max_depth
        # wake_up + good_night 两个边界槽位，加上 custom 事件槽位
        BOUNDARY_SLOTS_PER_DAY = 2
        slots_per_day = daily_events_count + BOUNDARY_SLOTS_PER_DAY

        # 估算 LLM 调用次数
        # 假设每个槽位触发满 chain_max_depth 次调用（实际 ≤ 预估值，chain 可能提前终止）
        dm_calls = days * slots_per_day * chain_max_depth
        char_reaction_calls = days * slots_per_day * chain_max_depth
        char_diary_calls = days
        sa_calls = days if life_sim.sa_agent else 0
        total_calls = dm_calls + char_reaction_calls + char_diary_calls + sa_calls

        # 获取模型名
        model = "unknown"
        try:
            if life_sim.dm_agent and hasattr(life_sim.dm_agent, "model"):
                model = life_sim.dm_agent.model or model
        except Exception as exc:
            logger.debug("获取 DM Agent model 名失败: %s", exc)

        if dry_run:
            return {
                "dry_run": True,
                "model": model,
                "estimate": {
                    "dm_calls": dm_calls,
                    "char_reaction_calls": char_reaction_calls,
                    "char_diary_calls": char_diary_calls,
                    "sa_calls": sa_calls,
                    "total_calls": total_calls,
                    "estimated_minutes": max(1, total_calls * 7 // 60),
                    "token_scale": f"{max(1, total_calls * 2)}k–{max(1, total_calls * 8)}k",
                },
            }

        # ── 执行 warp ──
        if start:
            start_dt = dt.datetime.fromisoformat(start)
            if start_dt.tzinfo is not None:
                start_dt = start_dt.replace(tzinfo=None)
        else:
            # 默认使用随机虚构日期，避免与真实墙钟混淆
            import random as _random
            y = _random.randint(1000, 1500)
            m = _random.randint(1, 12)
            d = _random.randint(1, 28)
            start_dt = dt.datetime(y, m, d, 8, 0, 0)
            logger.info(f"warp: 未指定 --start，使用随机虚构日期 {start_dt.strftime('%Y-%m-%d')}")

        stepped = SteppedClock(start_dt)
        original_clock = get_clock()
        set_clock(stepped)

        slots_processed = 0
        errors = 0
        skipped = 0

        import time as _time

        try:
            for day_idx in range(days):
                day_date = stepped.now().date()
                logger.info(f"── warp day {day_idx + 1}/{days} ({day_date}) ──")

                # 获取当日活动开始小时
                start_hour = getattr(
                    character.extensions, "event_day_start_hour", 8
                )
                end_hour = getattr(
                    character.extensions, "event_day_end_hour", 22
                )

                # 跨天重置 — advance_to_day() 返回当日槽位列表，同时重置内部状态
                slots = char_life.advance_to_day(day_date)
                for slot_idx, (slot_m, slot_type) in enumerate(slots):

                    # step_to 槽位时间
                    slot_hour = slot_m // 60
                    slot_min = slot_m % 60
                    slot_dt = dt.datetime.combine(
                        day_date, dt.time(slot_hour, slot_min)
                    )
                    stepped.step_to(slot_dt)

                    t0 = _time.monotonic()
                    slot_label = f"{slot_hour:02d}:{slot_min:02d} {slot_type}"
                    try:
                        await app.tick()
                        elapsed = _time.monotonic() - t0
                        slots_processed += 1
                        logger.info(
                            f"  [{slot_label}] OK ({elapsed:.1f}s)"
                        )
                    except Exception:
                        elapsed = _time.monotonic() - t0
                        logger.warning(
                            f"  [{slot_label}] FAIL ({elapsed:.1f}s) — "
                            f"day={day_idx} slot={slot_idx} type={slot_type}",
                            exc_info=True,
                        )
                        errors += 1

                # 日终处理
                day_end = dt.datetime.combine(day_date, dt.time(23, 59))
                stepped.step_to(day_end)

                t0 = _time.monotonic()
                try:
                    await life_sim.tick_daily()
                    elapsed = _time.monotonic() - t0
                    logger.info(f"  tick_daily OK ({elapsed:.1f}s)")
                except Exception:
                    elapsed = _time.monotonic() - t0
                    logger.warning(
                        f"  tick_daily FAIL ({elapsed:.1f}s) — day={day_idx}",
                        exc_info=True,
                    )
                    errors += 1

                # 进入下一天
                stepped.step_by(days=1)

            logger.info(
                f"warp 完成: {days} 天, {slots_processed} 槽位, "
                f"{errors} 错误, {skipped} 跳过"
            )

        finally:
            set_clock(original_clock)

        return {
            "dry_run": False,
            "days": days,
            "slots_processed": slots_processed,
            "errors": errors,
            "skipped": skipped,
            "total_calls_estimate": total_calls,
        }

    async def send(
        self,
        user_id: str,
        nickname: str,
        msg: str,
        group_id: str = "",
        dice_sequence: Optional[List[int]] = None,
        to_me: bool = False,
    ) -> Dict[str, Any]:
        """发送消息到 Bot

        Args:
            user_id: 用户ID
            nickname: 用户昵称
            msg: 消息内容
            group_id: 群组ID（空字符串表示私聊）
            dice_sequence: 可选的骰子序列

        Returns:
            包含输出文本和命令信息的字典
        """
        if not self._started or not self.bot:
            raise RuntimeError("Bot not started. Call start() first.")

        # 清空之前的输出
        self.proxy.clear()

        # 设置骰子序列
        token = None
        runtime = None
        if dice_sequence:
            runtime = SequenceRuntime(dice_sequence)
            token = set_runtime(runtime)

        try:
            # 构造消息元数据
            meta = MessageMetaData(
                plain_msg=msg,
                raw_msg=msg,
                sender=MessageSender(user_id, nickname),
                group_id=group_id,
                to_me=to_me,
            )

            # 处理消息
            commands = await self.bot.process_message(msg, meta)

            # 收集结果
            result = {
                "text": self.proxy.get_display_text(),
                "commands": self.proxy.to_dict_list(),
                "dice_consumed": runtime.get_consumed_count() if runtime else 0,
                "raw_command_count": len(commands),
            }

            return result

        finally:
            # 恢复骰子运行时
            if token is not None:
                reset_runtime(token)
