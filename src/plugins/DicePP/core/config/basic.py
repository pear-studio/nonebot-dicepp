import os

from utils.logger import dice_log
from utils.frozen import get_app_dir

PROJECT_PATH = get_app_dir()

def _data_dir_score(data_path: str) -> int:
    """
    为 Data 目录候选打分：优先选择更“像真实数据目录”的那个。
    主要用于 Linux 下大小写敏感导致的 Data/data 挂载不一致问题。
    """
    if not os.path.isdir(data_path):
        return -1
    expected_dirs = [
        "UserData",
        "Content",
    ]
    score = 0
    for name in expected_dirs:
        if os.path.isdir(os.path.join(data_path, name)):
            score += 2

    # 更细粒度的标志目录（无需知道 bot_id）
    if os.path.isdir(os.path.join(data_path, "Content", "QueryData")):
        score += 2
    if os.path.isdir(os.path.join(data_path, "Content", "DeckData")):
        score += 2
    if os.path.isdir(os.path.join(data_path, "Content", "RandomGenData")):
        score += 2
    if os.path.isdir(os.path.join(data_path, "UserData", "Bot")):
        score += 2
    if os.path.isdir(os.path.join(data_path, "UserData", "LocalImage")):
        score += 1
    return score


def _select_data_path(project_path: str) -> str:
    """
    在 Data 与 data 之间择优选择：
    - 若只有一个存在：用存在的；
    - 若两者都存在：用“更像数据目录”的那个（更高得分）；
    - 若都不存在：默认使用 Data（随后会创建）。
    """
    data_upper = os.path.join(project_path, "Data")
    data_lower = os.path.join(project_path, "data")

    upper_exists = os.path.isdir(data_upper)
    lower_exists = os.path.isdir(data_lower)

    if upper_exists and not lower_exists:
        return data_upper
    if lower_exists and not upper_exists:
        return data_lower

    if upper_exists and lower_exists:
        upper_score = _data_dir_score(data_upper)
        lower_score = _data_dir_score(data_lower)
        if lower_score > upper_score:
            dice_log(f"[Config] [Init] 检测到 Data/data 同时存在，选择更匹配的数据目录: {data_lower}")
            return data_lower
        return data_upper

    return data_upper


DATA_PATH = _select_data_path(PROJECT_PATH)

CONTENT_PATH = os.path.join(DATA_PATH, "Content")
USER_DATA_PATH = os.path.join(DATA_PATH, "UserData")

# 运行时数据（按 bot_id 拆分到子目录中）
BOT_DATA_PATH = os.path.join(USER_DATA_PATH, "Bot")
# 用户房规（HB*.db）也归类为“运行时用户数据”
QUERY_HOME_BREW_DATA_PATH = os.path.join(USER_DATA_PATH, "QueryHomebrew")

CONTENT_QUERY_DATA_PATH = os.path.join(CONTENT_PATH, "QueryData")
CONTENT_DECK_DATA_PATH = os.path.join(CONTENT_PATH, "DeckData")
CONTENT_RANDOM_GEN_DATA_PATH = os.path.join(CONTENT_PATH, "RandomGenData")
CONTENT_EXCEL_DATA_PATH = os.path.join(CONTENT_PATH, "ExcelData")

# localization.xlsx 里使用 IMG(...) 引用的本地图片资源（给所有 bot 共用也更合理）
LOCAL_IMG_PATH = os.path.join(USER_DATA_PATH, "LocalImage")


ALL_LOCAL_DIR_PATH = [
    DATA_PATH,
    USER_DATA_PATH,
    CONTENT_PATH,
    BOT_DATA_PATH,
    QUERY_HOME_BREW_DATA_PATH,
    CONTENT_QUERY_DATA_PATH,
    CONTENT_DECK_DATA_PATH,
    CONTENT_RANDOM_GEN_DATA_PATH,
    CONTENT_EXCEL_DATA_PATH,
    LOCAL_IMG_PATH,
]

for dirPath in ALL_LOCAL_DIR_PATH:
    if not os.path.exists(dirPath):
        os.makedirs(dirPath)
        dice_log("[Config] [Init] 创建文件夹: " + dirPath)
