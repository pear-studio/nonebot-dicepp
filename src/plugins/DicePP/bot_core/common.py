from data_manager import custom_data_chunk, DataChunkBase

DC_META = "meta"
DCP_META_ONLINE_LAST = ["online", "last"]
DCP_META_ONLINE_PERIOD = ["online", "period"]

DC_MACRO = "macro"
DC_VARIABLE = "variable"

DC_USER_DATA = "user_data"
DC_GROUP_DATA = "group_data"

DC_NICKNAME = "nickname"
NICKNAME_ERROR = "Undefined Name"


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
