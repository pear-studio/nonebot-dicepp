"""
掷骰适配器

将 module.roll 的调用隔离在 persona 模块外部，
负责异常转换和结果格式化。
"""
from nonebot.log import logger
from module.roll import RollDiceError
from module.roll.ast_engine.adapter import exec_roll_exp_unified
from .context import ToolContext
from .registry import ToolDef


class RollAdapter:
    """掷骰服务适配器"""

    @staticmethod
    def roll(expression: str) -> dict:
        """执行骰子表达式

        Args:
            expression: 骰子表达式，如 "1d20", "2d6+3"

        Returns:
            {"success": True, "value": str, "info": str, "exp": str}
            或 {"success": False, "error": str}
        """
        if not expression or len(expression) > 100:
            return {"success": False, "error": "表达式无效或过长（最大100字符）"}

        try:
            result = exec_roll_exp_unified(expression)
            return {
                "success": True,
                "value": result.get_val(),
                "info": result.get_info(),
                "exp": result.get_exp(),
            }
        except RollDiceError as e:
            return {"success": False, "error": f"掷骰失败: {e}\n请使用有效格式，如 1d20, 2d6+3, 1d20adv"}
        except Exception as e:
            return {"success": False, "error": "掷骰服务暂时不可用，请稍后再试"}


ROLL_DICE_TOOL = ToolDef(
    name="roll_dice",
    description="执行 TRPG 骰子表达式（如 1d20, 2d6+3, 1d20adv 等），返回掷骰结果",
    parameters={
        "type": "object",
        "properties": {
            "expression": {
                "type": "string",
                "description": "骰子表达式，如 '1d20'（掷一个d20）、'2d6+3'（掷两个d6加3）、'1d20adv'（优势掷骰）",
            }
        },
        "required": ["expression"],
    },
)


async def roll_dice_executor(args: dict, ctx: ToolContext) -> str:
    """执行掷骰工具"""
    expression = args.get("expression", "")
    result = RollAdapter.roll(expression)
    if not result["success"]:
        error = result["error"]
        if "暂时不可用" in error:
            logger.error(f"掷骰工具执行失败: {expression}")
        return error
    val = result["value"]
    info = result["info"]
    exp = result["exp"]
    if info and exp:
        return f"掷骰: {exp} = {info} = {val}"
    elif info:
        return f"掷骰: {expression} = {info} = {val}"
    else:
        return f"掷骰: {expression} = {val}"
