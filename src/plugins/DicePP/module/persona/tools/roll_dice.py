"""
掷骰适配器

将 module.roll 的调用隔离在 persona 模块外部，
负责异常转换和结果格式化。
"""
from module.roll import exec_roll_exp, RollDiceError


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
            result = exec_roll_exp(expression)
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
