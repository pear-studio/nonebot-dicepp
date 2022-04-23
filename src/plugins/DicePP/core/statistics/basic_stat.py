import json
import datetime
from typing import List, Dict

from core.data import JsonObject, custom_json_object

from utils.time import get_current_date_int, get_current_date_raw, int_to_datetime, datetime_to_int


class StatElementBase:
    def serialize(self) -> str:
        return f"{self.cur_day_val}|{self.last_day_val}|{self.total_val}|{self.update_time}"

    def deserialize(self, input_str: str) -> None:
        try:
            val_list = input_str.split("|")
            assert len(val_list) == 4
            val_list = [int(val) for val in val_list]
            self.cur_day_val, self.last_day_val, self.total_val, self.update_time = val_list
        except (AssertionError, ValueError):
            pass

    def __init__(self):
        self.cur_day_val: int = 0
        self.last_day_val: int = 0
        self.total_val: int = 0
        self.update_time: int = 0

    def __add__(self, other: "StatElementBase") -> "StatElementBase":
        res = StatElementBase()
        res.cur_day_val = self.cur_day_val + other.cur_day_val
        res.last_day_val = self.last_day_val + other.last_day_val
        res.total_val = self.total_val + other.total_val
        res.update_time = max(self.update_time, other.update_time)
        return res

    def inc(self, time: int = 1):
        self.cur_day_val += time
        self.total_val += time
        self.update_time = get_current_date_int()

    def clr(self):
        self.cur_day_val = 0
        self.last_day_val = 0
        self.total_val = 0
        self.update_time = get_current_date_int()

    def update(self, past_days: int = 1):
        if past_days == 1:
            self.last_day_val = self.cur_day_val
            self.cur_day_val = 0
        elif past_days > 1:
            self.last_day_val = 0
            self.cur_day_val = 0


class UserCommandStatInfo:
    def serialize(self) -> str:
        flag_list = []
        for flag, elem in self.flag_dict.items():
            flag_list.append(f"{flag}|{elem.serialize()}")
        return "&".join(flag_list)

    def deserialize(self, input_str: str) -> None:
        if not input_str.strip():
            return
        flag_list = input_str.split("&")
        for flag_info in flag_list:
            flag_str, elem_str = flag_info.split("|", maxsplit=1)
            self.flag_dict[int(flag_str)] = StatElementBase()
            self.flag_dict[int(flag_str)].deserialize(elem_str)

    def __init__(self):
        self.flag_dict: Dict[int, StatElementBase] = {}

    def __add__(self, other: "UserCommandStatInfo") -> "UserCommandStatInfo":
        res = UserCommandStatInfo()
        from core.command import DPP_COMMAND_FLAG_DICT
        for flag in DPP_COMMAND_FLAG_DICT:
            if flag not in other.flag_dict and flag not in self.flag_dict:
                continue
            if flag not in res.flag_dict:
                res.flag_dict[flag] = StatElementBase()
            if flag in self.flag_dict:
                res.flag_dict[flag] += self.flag_dict[flag]
            if flag in other.flag_dict:
                res.flag_dict[flag] += other.flag_dict[flag]
        return res

    def record(self, command):
        from core.command import UserCommandBase, DPP_COMMAND_FLAG_DICT
        command: UserCommandBase
        for flag in DPP_COMMAND_FLAG_DICT.keys():
            if flag & command.flag:
                if command.flag not in self.flag_dict:
                    self.flag_dict[command.flag] = StatElementBase()
                self.flag_dict[command.flag].inc()

    def update(self, past_days: int = 1):
        for elem in self.flag_dict.values():
            elem.update(past_days)


class D20StatInfo:
    def serialize(self) -> str:
        val_list = self.cur_list + self.last_list + self.total_list
        val_list = [str(val) for val in val_list]
        return "|".join(val_list)

    def deserialize(self, input_str: str) -> None:
        val_list = input_str.split("|")
        val_list = [int(val) for val in val_list]
        self.cur_list = val_list[:20]
        self.last_list = val_list[20:40]
        self.total_list = val_list[40:]

    def __init__(self):
        self.cur_list = [0] * 20
        self.last_list = [0] * 20
        self.total_list = [0] * 20

    def record(self, d20_val: int):
        if d20_val < 1 or d20_val > 20:
            return
        self.cur_list[d20_val-1] += 1
        self.total_list[d20_val-1] += 1

    def update(self):
        self.last_list = self.cur_list
        self.cur_list = [0] * 20


class RollStatInfo:
    def serialize(self) -> str:
        return "&".join([self.times.serialize(), self.d20.serialize()])

    def deserialize(self, input_str: str) -> None:
        val_list = input_str.split("&")
        self.times.deserialize(val_list[0])
        self.d20.deserialize(val_list[1])

    def __init__(self):
        self.times: StatElementBase = StatElementBase()
        self.d20: D20StatInfo = D20StatInfo()

    def record(self):
        self.times.inc()

    def record_d20(self, d20_val: int):
        self.times.inc()
        self.d20.record(d20_val)

    def update(self):
        self.times.update()
        self.d20.update()


@custom_json_object
class MetaStatInfo(JsonObject):
    def serialize(self) -> str:
        json_dict = {"online_period": [[datetime_to_int(start_period), datetime_to_int(end_period)] for
                                       start_period, end_period in self.online_period],
                     "msg": self.msg.serialize(),
                     "cmd": self.cmd.serialize(),
                     }
        return json.dumps(json_dict)

    def deserialize(self, json_str: str) -> None:
        json_dict: dict = json.loads(json_str)
        online_time_period: List[List[int, int]] = json_dict["online_period"]
        self.online_period = [[int_to_datetime(time_period[0]), int_to_datetime(time_period[1])] for time_period in online_time_period]
        self.msg.deserialize(json_dict["msg"])
        self.cmd.deserialize(json_dict["cmd"])

    def __init__(self):
        self.online_period: List[List[datetime.datetime]] = []
        self.msg: StatElementBase = StatElementBase()
        self.cmd: UserCommandStatInfo = UserCommandStatInfo()

    def stat_msg(self, time: int = 1):
        self.msg.inc(time)

    def stat_cmd(self, command):
        self.cmd.record(command)

    def update(self, is_first_time: bool = False) -> bool:
        current_date = get_current_date_raw()
        if is_first_time:
            self.online_period.append([current_date, current_date])

        should_tick_daily: bool = False
        if current_date.date() != self.online_period[-1][-1].date():  # 最后在线时间和当前时间不是同一天
            should_tick_daily = True
            self.msg.update()
            self.cmd.update()

        self.online_period[-1][-1] = current_date
        return should_tick_daily
