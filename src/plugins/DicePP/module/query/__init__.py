from .query_command import QueryCommand
from .homebrew_command import HomebrewCommand
# 私设新指令族（add/del/list/宏/db），priority=1 早于 HomebrewCommand
from .hb_command import HBExtCommand