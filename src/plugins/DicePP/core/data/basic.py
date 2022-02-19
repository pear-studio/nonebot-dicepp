from core.data.data_chunk import custom_data_chunk, DataChunkBase

DC_META = "meta"
DCP_META_ONLINE = ["online"]
DCP_META_ONLINE_LAST = DCP_META_ONLINE + ["last"]
DCP_META_ONLINE_PERIOD = DCP_META_ONLINE + ["period"]
DCP_META_MSG = ["message"]
DCP_META_MSG_TOTAL_NUM = DCP_META_MSG + ["total_num"]
DCP_META_MSG_TODAY_NUM = DCP_META_MSG + ["today_num"]
DCP_META_MSG_LAST_NUM = DCP_META_MSG + ["last_num"]
DCP_META_CMD = ["command"]
DCP_META_CMD_TOTAL_NUM = DCP_META_CMD + ["total_num"]
DCP_META_CMD_TODAY_NUM = DCP_META_CMD + ["today_num"]
DCP_META_CMD_LAST_NUM = DCP_META_CMD + ["last_num"]

DC_MACRO = "macro"
DC_VARIABLE = "variable"

DC_USER_DATA = "user_data"
DC_GROUP_DATA = "group_data"

DC_NICKNAME = "nickname"


@custom_data_chunk(identifier=DC_META)
class _(DataChunkBase):
    def __init__(self):
        super().__init__()


@custom_data_chunk(identifier=DC_MACRO, include_json_object=True)
class _(DataChunkBase):
    def __init__(self):
        super().__init__()


@custom_data_chunk(identifier=DC_VARIABLE, include_json_object=True)
class _(DataChunkBase):
    def __init__(self):
        super().__init__()


@custom_data_chunk(identifier=DC_USER_DATA)
class _(DataChunkBase):
    def __init__(self):
        super().__init__()


@custom_data_chunk(identifier=DC_GROUP_DATA)
class _(DataChunkBase):
    def __init__(self):
        super().__init__()


@custom_data_chunk(identifier=DC_NICKNAME)
class _(DataChunkBase):
    def __init__(self):
        super().__init__()
