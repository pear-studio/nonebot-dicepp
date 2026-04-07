from core.config.basic import (
    PROJECT_PATH,
    DATA_PATH,
    CONTENT_PATH,
    USER_DATA_PATH,
    BOT_DATA_PATH,
    CONTENT_QUERY_DATA_PATH,
    CONTENT_DECK_DATA_PATH,
    CONTENT_RANDOM_GEN_DATA_PATH,
    CONTENT_EXCEL_DATA_PATH,
    LOCAL_IMG_PATH,
)
from core.config.common import *
from core.config.declare import BOT_VERSION, BOT_DESCRIBE, BOT_GIT_LINK
from core.config.pydantic_models import BotConfig
from core.config.loader import ConfigLoader, ConfigValidationError
