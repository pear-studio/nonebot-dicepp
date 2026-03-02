from .jrrp_command import JrrpCommand
from .dnd_command import UtilsDNDCommand
from .coc_command import UtilsCOCCommand
from .statistics_cmd import StatisticsCommand

# 防止无log情况下无法运行
try:
    from .log_command import LogCommand
except:
    a = 1+1 # 不加载了

from .test_command import NewTestCommand