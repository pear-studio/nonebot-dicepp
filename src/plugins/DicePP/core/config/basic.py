import os

from utils.logger import dice_log

PROJECT_PATH = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))

DATA_PATH = os.path.join(PROJECT_PATH, 'Data')

BOT_DATA_PATH = os.path.join(DATA_PATH, 'Bot')
CONFIG_PATH = os.path.join(DATA_PATH, 'Config')
LOCAL_IMG_PATH = os.path.join(CONFIG_PATH, 'LocalImage')


ALL_LOCAL_DIR_PATH = [DATA_PATH, BOT_DATA_PATH, CONFIG_PATH, LOCAL_IMG_PATH]

for dirPath in ALL_LOCAL_DIR_PATH:
    if not os.path.exists(dirPath):
        os.makedirs(dirPath)
        dice_log("[Config] [Init] 创建文件夹: " + dirPath)