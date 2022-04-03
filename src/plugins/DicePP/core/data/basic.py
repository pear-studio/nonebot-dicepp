from core.data.data_chunk import custom_data_chunk, DataChunkBase

DCK_TOTAL_NUM = "total_num"
DCK_TODAY_NUM = "today_num"
DCK_LAST_NUM = "last_num"
DCK_LAST_TIME = "last_time"

DC_META = "meta"
DCP_META_ONLINE = ["online"]
DCP_META_ONLINE_LAST = DCP_META_ONLINE + ["last"]
DCP_META_ONLINE_PERIOD = DCP_META_ONLINE + ["period"]
DCP_META_MSG = ["message"]
DCP_META_MSG_TOTAL_NUM = DCP_META_MSG + [DCK_TOTAL_NUM]
DCP_META_MSG_TODAY_NUM = DCP_META_MSG + [DCK_TODAY_NUM]
DCP_META_MSG_LAST_NUM = DCP_META_MSG + [DCK_LAST_NUM]
DCP_META_CMD = ["command"]
DCP_META_CMD_TOTAL_NUM = DCP_META_CMD + [DCK_TOTAL_NUM]
DCP_META_CMD_TODAY_NUM = DCP_META_CMD + [DCK_TODAY_NUM]
DCP_META_CMD_LAST_NUM = DCP_META_CMD + [DCK_LAST_NUM]

DC_MACRO = "macro"
DC_VARIABLE = "variable"

DC_USER_DATA = "user_data"
DCP_USER_MSG_A_UID = DCP_META_MSG
DCP_USER_CMD_FLAG_A_UID = DCP_META_CMD + ["flag"]
DCP_USER_CMD_CLU_A_UID = DCP_META_CMD + ["cluster"]
DCP_USER_META_A_UID = ["meta"]

DC_GROUP_DATA = "group_data"
DCP_GROUP_MSG_A_GID = DCP_USER_MSG_A_UID
DCP_GROUP_CMD_FLAG_A_GID = DCP_USER_CMD_FLAG_A_UID
DCP_GROUP_CMD_CLU_A_GID = DCP_USER_CMD_CLU_A_UID
DCP_GROUP_INFO_A_GID = ["info"]
DCP_GROUP_META_A_GID = DCP_USER_META_A_UID

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
