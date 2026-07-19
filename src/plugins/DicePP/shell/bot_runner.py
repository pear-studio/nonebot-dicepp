"""Bot 运行包装器 - 管理 Bot 实例、捕获输出、控制骰子"""

import asyncio
import datetime as dt
import os
from itertools import count
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

# Shell bot_runner 全部使用裸绝对导入 (core.X / utils.X / module.X / adapter)，
# 与核心模块一致。不要改回 ..core.bot 相对导入： shell 以
# plugins.DicePP.shell.bot_runner 路径被导入时会触发 sys.modules 双副本，
# 导致 ContextVar 读写分离。
from core.bot import Bot
from core.config import Paths
from core.communication import (
    GroupInfo,
    GroupMemberInfo,
    MessageMetaData,
    MessageSender,
    PostSendEvent,
)
from core.message_types import MessageType
from utils.logger import logger, restore_runtime_logging
from adapter import ClientProxy
from core.command import (
    BotCommandBase,
    BotCommandDispatchResult,
    BotSendFileCommand,
    BotSendMsgCommand,
    FileDeliveryOutcome,
    FileDeliveryResult,
)
from utils.sequence_runtime import SequenceRuntime
# 注意：必须使用裸绝对导入 `module.roll.karma_runtime`，
# 因为 Bot 内部大量使用该路径导入此模块。
# 若使用相对导入 (`..module.roll.karma_runtime`)，会导致
# `sys.modules` 中出现两个副本，ContextVar 的读写将分离，
# 从而使 `--dice` 序列控制完全失效。
from module.roll.karma_runtime import set_runtime, reset_runtime
from .session import bot_id_for_session


class CaptureProxy(ClientProxy):
    """捕获 Bot 输出，而不是真的发送到 QQ"""

    def __init__(self):
        super().__init__()
        self.commands: List[BotCommandBase] = []
        self.bot: Bot | None = None
        self._message_ids = count(1)

    def bind_bot(self, bot: Bot | None) -> None:
        self.bot = bot

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
        deliveries = ()
        if (
            isinstance(command, BotSendMsgCommand)
            and self.bot is not None
            and not command.skip_history_record
        ):
            message_type = MessageType.from_str(command.message_type).value
            for target in command.targets:
                await self.bot.dispatch_post_send_event(
                    PostSendEvent(
                        group_id=target.group_id or None,
                        user_id=(
                            str(self.bot.account)
                            if target.group_id
                            else target.user_id
                        ),
                        role="assistant",
                        message_type=message_type,
                        content=command.msg,
                        display_name="我",
                        platform_message_id=(
                            f"shell-message-{next(self._message_ids)}"
                        ),
                        history_stream_id=command.msg_id,
                    )
                )
        if isinstance(command, BotSendFileCommand):
            folder = (
                command.display_name.split("/", 1)[0]
                if "/" in command.display_name
                else None
            )
            deliveries = tuple(
                FileDeliveryResult(
                    target=target,
                    outcome=(
                        FileDeliveryOutcome.FOLDER_SUCCESS
                        if folder
                        else FileDeliveryOutcome.ROOT_SUCCESS
                    ),
                    requested_folder=folder,
                )
                for target in command.targets
            )
        return BotCommandDispatchResult(
            command=command,
            file_deliveries=deliveries,
        )

    async def process_bot_command_list(self, command_list: List[BotCommandBase]):
        """捕获命令列表"""
        return [await self.process_bot_command(command) for command in command_list]

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
            elif isinstance(cmd, BotSendFileCommand):
                result.append({
                    "type": "send_file",
                    "file": cmd.file,
                    "display_name": cmd.display_name,
                })
            else:
                result.append({
                    "type": cmd.__class__.__name__,
                })
        return result


class BotRunner:
    """管理单个 Bot 实例的生命周期和交互"""

    _MAX_WAIT_ITERATIONS = 30
    _POLL_INTERVAL = 0.5

    def __init__(self, session_dir: Path, *, tick: bool = False):
        self.session_dir = session_dir
        self.tick = tick
        self.bot: Optional[Bot] = None
        self.proxy = CaptureProxy()
        self._started = False
        self._runtime_started_at: dt.datetime | None = None
        self._runtime_clock_original: Any | None = None
        self._warp_clock: Any | None = None

    @property
    def started(self) -> bool:
        """Whether the Bot lifecycle is currently active."""
        return self._started

    @property
    def dashboard_control_enabled(self) -> bool:
        """Whether the running Bot has an open dashboard control channel."""
        return self.bot is not None and self.bot._control_channel is not None

    async def start(self) -> None:
        """启动 Bot 实例"""
        if self._started:
            return

        try:
            from utils.time import get_clock

            self._runtime_clock_original = get_clock()
            self._activate_workspace()

            # 创建 Bot 实例
            account = bot_id_for_session(self.session_dir.name)
            self.bot = Bot(account=account, no_tick=not self.tick)
            self.proxy.bind_bot(self.bot)

            # 配置
            self.bot.config.master = ["shell_master"]
            self.bot.set_client_proxy(self.proxy)

            # 初始化
            await self.bot.delay_init_command()

            # no_tick=True 时 tick_loop 不运行，需要手动处理待办任务（如 persona 异步初始化）
            if self.bot._no_tick and self.bot.scheduler.pending:
                completed = False
                for _ in range(BotRunner._MAX_WAIT_ITERATIONS):  # 最多等待 30 秒
                    await self.bot.scheduler.process(BotRunner._POLL_INTERVAL)
                    if not self.bot.scheduler.pending:
                        completed = True
                        break
                    await asyncio.sleep(BotRunner._POLL_INTERVAL)
                if completed:
                    logger.info("pending tasks 处理完成")
                else:
                    logger.warning("pending tasks 等待超时（30s），仍有未完成任务")

            # 默认 warp 起点应是 Runtime 已完成初始化、真正可接收请求的时刻，
            # 而不是 provider probe / Persona 初始化开始之前。
            self._runtime_started_at = self._runtime_clock_original.now()
            self._started = True
        except BaseException:
            if self.bot is not None:
                try:
                    await self.bot.shutdown_async()
                except Exception:
                    logger.exception("Shell Bot startup cleanup failed")
                self.bot = None
            self.proxy.bind_bot(None)
            self._runtime_started_at = None
            self._runtime_clock_original = None
            raise

    async def stop(self) -> None:
        """停止 Bot 实例"""
        try:
            if self.bot and self._started:
                await self.bot.shutdown_async()
        finally:
            if self._runtime_clock_original is not None:
                from utils.time import set_clock

                set_clock(self._runtime_clock_original)
            self.bot = None
            self.proxy.bind_bot(None)
            self._started = False
            self._runtime_started_at = None
            self._runtime_clock_original = None
            self._warp_clock = None

    def _activate_workspace(self) -> None:
        """Point Paths, env vars, and loguru sinks at the session workspace.

        serve_session is the process terminus — the process exits when the
        server shuts down — so there is no need to save and restore the
        previous state. The redirect is one-way and permanent for this process.
        """
        workspace = str(self.session_dir.resolve())
        os.environ["DICEPP_PROJECT_ROOT"] = workspace
        os.environ["DICEPP_APP_DIR"] = workspace
        Paths.configure_project_root(workspace)
        restore_runtime_logging()

    async def warp(
        self,
        days: int,
        start: str | None = None,
        dry_run: bool = False,
        progress: Callable[[dict[str, Any]], None] | None = None,
    ) -> dict:
        """从 Runtime 当前时间线连续推进指定天数。

        Args:
            days: 连续推进的 24 小时周期数
            start: 首次 warp 的起始时间（ISO 格式），默认 Runtime 启动时间
            dry_run: 仅预估成本，不实际执行
            progress: 每推进一个模拟小时后接收进度快照

        Returns:
            包含执行结果的字典
        """
        from utils.time import SteppedClock, set_clock
        from utils.logger import logger

        if not self._started or not self.bot:
            raise RuntimeError("Bot not started. Call start() first.")
        if days < 1:
            raise ValueError("days must be at least 1")
        if start and self._warp_clock is not None:
            raise RuntimeError(
                "--start is only allowed before the Runtime timeline has advanced"
            )

        # 获取 PersonaCommand → PersonaApp → LifeSimulator（与 dicebot.py 一致的 isinstance 查找）
        from module.persona.command import PersonaCommand as PCmd
        persona_cmd = next(
            (cmd for cmd in self.bot.command_dict.values() if isinstance(cmd, PCmd)),
            None,
        )
        if persona_cmd is None or persona_cmd.app is None:
            raise RuntimeError(
                "Persona 模块未初始化。请检查该 session 的 Persona、角色卡和 "
                "provider 配置，以及 serve 启动日志。"
            )

        app = persona_cmd.app
        life_sim = app.life
        char_life = life_sim.character_life

        if char_life is None:
            raise RuntimeError("CharacterLife 未初始化")

        config = char_life.config
        character = char_life.character
        persona_config = self.bot.config.persona_ai

        if start:
            start_dt = dt.datetime.fromisoformat(start)
            if start_dt.tzinfo is not None:
                start_dt = start_dt.replace(tzinfo=None)
        elif self._warp_clock is not None:
            start_dt = self._warp_clock.now()
        elif self._runtime_started_at is not None:
            start_dt = self._runtime_started_at
        else:
            raise RuntimeError("Runtime start time is unavailable")

        total_minutes = days * 24 * 60
        end_dt = start_dt + dt.timedelta(minutes=total_minutes)
        last_included_dt = end_dt - dt.timedelta(minutes=1)
        calendar_days_touched = (
            last_included_dt.date() - start_dt.date()
        ).days + 1

        # 连续 24 小时可能覆盖两个部分日期；严格上界按半开窗口实际触及
        # 的日历日数量计算，避免把 days * slots_per_day 误称为 max。
        daily_events_count = getattr(
            character.extensions, "daily_events_count", 3
        )
        chain_max_depth = config.chain_max_depth
        BOUNDARY_SLOTS_PER_DAY = 2
        slots_per_day = daily_events_count + BOUNDARY_SLOTS_PER_DAY

        # 以 Agent Run 为单位给出上界。Life 链可能提前结束，因此 DM /
        # Character 的实际 Run 数通常低于此值。
        life_slot_runs_max = calendar_days_touched * slots_per_day
        dm_runs = life_slot_runs_max * chain_max_depth
        char_reaction_runs = life_slot_runs_max * chain_max_depth
        daily_runs_max = days
        diary_runs_max = daily_runs_max
        sa_runs_max = daily_runs_max if life_sim.sa_agent else 0

        proactive_labels: list[str] = []
        proactive_occurrences: set[tuple[dt.date, str]] = set()
        share_scheduler = getattr(life_sim, "share_scheduler", None)
        if (
            share_scheduler is not None
            and persona_config.proactive_share_schedule_enabled
        ):
            schedule: list[tuple[str, int]] = []
            if persona_config.proactive_share_schedule_morning_enabled:
                start_hour = character.extensions.event_day_start_hour
                if start_hour is not None and start_hour > 0:
                    schedule.append(("morning", (start_hour * 60 + 5) % 1440))
            for value in persona_config.proactive_share_schedule_times:
                try:
                    hour, minute = map(int, value.split(":", 1))
                except (ValueError, AttributeError):
                    continue
                schedule.append(
                    (f"midday_{value}", (hour * 60 + minute) % 1440)
                )
            if persona_config.proactive_share_schedule_evening_enabled:
                end_hour = character.extensions.event_day_end_hour
                if end_hour is not None and end_hour > 0:
                    schedule.append(("evening", (end_hour * 60 - 5) % 1440))

            proactive_labels = [label for label, _ in schedule]
            jitter = persona_config.proactive_share_schedule_jitter_minutes
            cursor = start_dt
            while cursor < end_dt:
                minute_of_day = cursor.hour * 60 + cursor.minute
                for label, center_minute in schedule:
                    if self._minute_in_jitter_window(
                        minute_of_day,
                        center_minute,
                        jitter,
                    ):
                        proactive_occurrences.add((cursor.date(), label))
                cursor += dt.timedelta(minutes=1)

        force_targets = len({
            value for value in persona_config.proactive_always_send_users if value
        }) + len({
            value for value in persona_config.proactive_always_send_groups if value
        })
        proactive_runs_max = len(proactive_occurrences) * force_targets

        # 获取模型名
        model = "unknown"
        try:
            if life_sim.dm_agent and hasattr(life_sim.dm_agent, "model"):
                model = life_sim.dm_agent.model or model
        except Exception as exc:
            logger.debug("获取 DM Agent model 名失败: {}", exc)

        if dry_run:
            return {
                "dry_run": True,
                "model": model,
                "start_at": start_dt.isoformat(),
                "end_at": end_dt.isoformat(),
                "minutes": total_minutes,
                "estimate": {
                    "calendar_days_touched": calendar_days_touched,
                    "dm_agent_runs_max": dm_runs,
                    "character_reaction_runs_max": char_reaction_runs,
                    "diary_agent_runs_max": diary_runs_max,
                    "sa_agent_runs_max": sa_runs_max,
                    "proactive_agent_runs_max": proactive_runs_max,
                    "proactive_schedule_windows": len(proactive_occurrences),
                    "proactive_labels": proactive_labels,
                    "background_max_rounds": (
                        persona_config.background_llm_max_rounds
                    ),
                    "sa_max_rounds": persona_config.sa_max_rounds,
                },
            }

        # ── 执行 warp ──
        if self._warp_clock is None:
            self._warp_clock = SteppedClock(start_dt)
        stepped = self._warp_clock
        set_clock(stepped)

        minutes_advanced = 0
        life_slots_marked = 0
        tick_errors = 0
        daily_runs = 0
        daily_errors = 0
        fired_proactive: set[tuple[str, str]] = set()
        preexisting_proactive: set[tuple[str, str]] = set()

        def _proactive_markers(
            scheduler: object,
            fallback_date: dt.date,
        ) -> set[tuple[str, str]]:
            fired_dates = getattr(scheduler, "_fired_dates", None)
            if isinstance(fired_dates, dict):
                return {
                    (str(fired_date), str(label))
                    for label, fired_date in fired_dates.items()
                }
            fired_date = getattr(
                scheduler, "_last_event_date", None
            ) or fallback_date.isoformat()
            return {
                (str(fired_date), str(label))
                for label in getattr(scheduler, "_fired_times", set())
            }

        if share_scheduler is not None:
            preexisting_proactive = _proactive_markers(
                share_scheduler, start_dt.date()
            )

        for minute_idx in range(total_minutes):
            current = stepped.now()
            life_date_before = getattr(char_life, "_last_event_date", None)
            fired_before = set(
                getattr(char_life, "_fired_slot_indices", set())
            )
            try:
                await app.tick()
            except asyncio.CancelledError:
                raise
            except Exception:
                tick_errors += 1
                logger.warning(
                    "warp tick 失败: clock={} minute={}/{}",
                    current.isoformat(),
                    minute_idx + 1,
                    total_minutes,
                    exc_info=True,
                )

            fired_after = set(
                getattr(char_life, "_fired_slot_indices", set())
            )
            life_date_after = getattr(char_life, "_last_event_date", None)
            if life_date_after != life_date_before:
                life_slots_marked += len(fired_after)
            else:
                life_slots_marked += max(
                    0,
                    len(fired_after) - len(fired_before),
                )

            if share_scheduler is not None:
                for marker in _proactive_markers(
                    share_scheduler, current.date()
                ):
                    if marker not in preexisting_proactive:
                        fired_proactive.add(marker)

            next_minute = current + dt.timedelta(minutes=1)
            if next_minute.date() != current.date():
                try:
                    await life_sim.tick_daily()
                except asyncio.CancelledError:
                    raise
                except Exception:
                    daily_errors += 1
                    logger.warning(
                        "warp tick_daily 失败: date={}",
                        current.date().isoformat(),
                        exc_info=True,
                    )
                daily_runs += 1

            stepped.step_to(next_minute)
            minutes_advanced += 1

            if (
                progress is not None
                and minutes_advanced % 60 == 0
            ):
                progress({
                    "day": minutes_advanced // (24 * 60),
                    "days": days,
                    "hours_advanced": minutes_advanced // 60,
                    "total_hours": days * 24,
                    "minutes_advanced": minutes_advanced,
                    "total_minutes": total_minutes,
                    "life_slots_marked": life_slots_marked,
                    "tick_errors": tick_errors,
                    "daily_runs": daily_runs,
                    "daily_errors": daily_errors,
                })

        return {
            "dry_run": False,
            "days": days,
            "start_at": start_dt.isoformat(),
            "end_at": stepped.now().isoformat(),
            "minutes_advanced": minutes_advanced,
            "life_slots_marked": life_slots_marked,
            "tick_errors": tick_errors,
            "proactive_schedule_count": len(fired_proactive),
            "proactive_schedule_labels": sorted({
                label for _, label in fired_proactive
            }),
            "daily_runs": daily_runs,
            "daily_errors": daily_errors,
        }

    @staticmethod
    def _minute_in_jitter_window(
        minute_of_day: int,
        center_minute: int,
        jitter_minutes: int,
    ) -> bool:
        """Return whether a minute falls in a cyclic ±jitter schedule window."""
        if jitter_minutes <= 0:
            return minute_of_day == center_minute
        distance = abs(minute_of_day - center_minute)
        return min(distance, 1440 - distance) <= jitter_minutes

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
            # 私聊对齐生产适配器：私聊事件 to_me 永远为 True
            # 注：MessageMetaData.__init__ 也包含同逻辑 auto-correction，
            # 此处提前置位是为了避免 shell 场景下触发 constructor 的 warning 日志。
            if not group_id:
                to_me = True
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
