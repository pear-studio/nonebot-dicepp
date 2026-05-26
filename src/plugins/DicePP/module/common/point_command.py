"""点数指令 (.point)

简单的用户点数系统：每天活跃用户自动获得点数，可用于游戏化奖励。
基于 α 已有的 UserPoint 模型，新增 point Repository（v3 migration）。

配置项通过 BotConfig.point 注入（pydantic_models.PointConfig），
master 可通过修改 config 文件 + 热重载（reload_config 指令）调整。
"""
from datetime import datetime
from typing import Any, List, Tuple

from core.bot import Bot
from core.command import (
    BotCommandBase,
    BotSendMsgCommand,
    UserCommandBase,
    custom_user_command,
)
from core.command.const import DPP_COMMAND_PRIORITY_DEFAULT
from core.communication import (
    GroupMessagePort,
    MessageMetaData,
    PrivateMessagePort,
)
from core.data.models import UserPoint


LOC_POINT_SHOW   = "point_show"
LOC_POINT_LACK   = "point_lack"
LOC_POINT_CHECK  = "point_check"
LOC_POINT_EDIT   = "point_edit"
LOC_POINT_ERROR  = "point_edit_error"


async def _get_or_create_point(bot: Bot, user_id: str, init_val: int) -> UserPoint:
    p = await bot.db.point.get(user_id)
    if p is None:
        p = UserPoint(user_id=user_id, cur_point=init_val, today_point=0,
                      last_update=datetime.now())
        await bot.db.point.save(p)
    return p


@custom_user_command(readable_name="点数指令", priority=DPP_COMMAND_PRIORITY_DEFAULT)
class PointCommand(UserCommandBase):
    """.point 查看 / .point set <id> <val>（master） / .point get <id>（master）"""

    def __init__(self, bot: Bot):
        super().__init__(bot)
        bot.loc_helper.register_loc_text(LOC_POINT_SHOW,
            "{name} 的点数：{point}（今日已用 {today}/{limit}）",
            "用户查看自己的点数")
        bot.loc_helper.register_loc_text(LOC_POINT_LACK,
            "点数不足：{reason}",
            "扣点失败")
        bot.loc_helper.register_loc_text(LOC_POINT_CHECK,
            "{id} 的点数：{point}",
            "管理员查询其他用户点数")
        bot.loc_helper.register_loc_text(LOC_POINT_EDIT,
            "已调整：{id} 的点数 {old} → {new}",
            "管理员调整其他用户点数")
        bot.loc_helper.register_loc_text(LOC_POINT_ERROR,
            "点数处理出错：{error}",
            "出错")

    # ── 内部封装：从 BotConfig 取实时配置，支持热重载 ────────────────────
    @property
    def _cfg(self):
        return self.bot.config.point

    def can_process_msg(self, msg_str: str, meta: MessageMetaData) -> Tuple[bool, bool, Any]:
        if msg_str.startswith(".point"):
            return True, False, msg_str[6:].strip()
        return False, False, None

    async def process_msg(self, msg_str: str, meta: MessageMetaData,
                          hint: Any) -> List[BotCommandBase]:
        port = (GroupMessagePort(meta.group_id)
                if meta.group_id else PrivateMessagePort(meta.user_id))
        arg = hint or ""
        is_master = getattr(meta, "permission", 0) >= 3
        cfg = self._cfg

        try:
            if not arg:
                # 查询自己
                p = await _get_or_create_point(self.bot, meta.user_id, cfg.init)
                self._refresh_daily(p)
                await self.bot.db.point.save(p)
                feedback = self.format_loc(
                    LOC_POINT_SHOW,
                    name=meta.nickname or meta.user_id,
                    point=p.cur_point, today=p.today_point,
                    limit=cfg.limit_daily,
                )
            elif arg.startswith("set") and is_master:
                # .point set <user_id> <value>
                parts = arg[3:].strip().split()
                if len(parts) != 2:
                    raise ValueError("用法：.point set <user_id> <value>")
                target_id, value_str = parts
                value = int(value_str)
                p = await _get_or_create_point(self.bot, target_id, cfg.init)
                old = p.cur_point
                # 上限放宽到 max * 10 给 master 临时调整余地
                p.cur_point = max(0, min(value, cfg.max * 10))
                p.last_update = datetime.now()
                await self.bot.db.point.save(p)
                feedback = self.format_loc(
                    LOC_POINT_EDIT, id=target_id, old=old, new=p.cur_point
                )
            elif arg.startswith("get") and is_master:
                # .point get <user_id>
                target_id = arg[3:].strip()
                if not target_id:
                    raise ValueError("用法：.point get <user_id>")
                p = await self.bot.db.point.get(target_id)
                point_val = p.cur_point if p else 0
                feedback = self.format_loc(
                    LOC_POINT_CHECK, id=target_id, point=point_val
                )
            else:
                feedback = ("用法：\n"
                            "  .point          查看自己的点数\n"
                            "  .point set <id> <值>  (管理员) 设置\n"
                            "  .point get <id>       (管理员) 查询")
        except ValueError as e:
            feedback = self.format_loc(LOC_POINT_ERROR, error=str(e))

        return [BotSendMsgCommand(self.bot.account, feedback, [port])]

    def _refresh_daily(self, p: UserPoint) -> None:
        """跨天则重置 today_point 并补发每日点数。

        tick_daily 是同步接口而 DB 操作是 async，无法在 tick_daily 里直接
        async-save。改用"懒补发"：用户下次访问 .point 时检测到跨天，
        就重置 today_point 并按需 +cfg.add（不超 cfg.max）。
        """
        cfg = self._cfg
        now = datetime.now()
        if p.last_update.date() != now.date():
            if p.cur_point <= cfg.max:
                p.cur_point = min(cfg.max, p.cur_point + cfg.add)
            p.today_point = 0
            p.last_update = now

    def get_help(self, keyword: str, meta: MessageMetaData) -> str:
        if keyword == "point":
            cfg = self._cfg
            return (
                "点数指令：\n"
                f"  .point          查看自己的点数（初始 {cfg.init}，每日补 {cfg.add}，上限 {cfg.max}）\n"
                f"  每日消耗上限 {cfg.limit_daily} 点\n"
                "  .point set <user_id> <值>   (管理员) 设置\n"
                "  .point get <user_id>        (管理员) 查询"
            )
        return ""

    def get_description(self) -> str:
        return ".point 查询/管理点数"
