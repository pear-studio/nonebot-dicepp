"""
roll_const.py — roll 模块共享常量

将跨文件引用的常量集中在此处，避免重复定义导致语义漂移。
"""

#: 多轮掷骰上限次数（`#` 或 BAB 推导结果超出此值则回退为 1）
MULTI_ROLL_LIMIT: int = 10
