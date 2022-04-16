from core.data.data_chunk import custom_data_chunk, DataChunkBase

DC_META = "meta"
DCK_META_STAT = "stat"

DC_MACRO = "macro"
DC_VARIABLE = "variable"

DC_USER_DATA = "user_data"
DCK_USER_STAT = "stat"

DC_GROUP_DATA = "group_data"
DCK_GROUP_STAT = "stat"

DC_NICKNAME = "nickname"


@custom_data_chunk(identifier=DC_META, include_json_object=True)
class _(DataChunkBase):
    def __init__(self):
        super().__init__()
        self.version: int = 0

    def introspect(self) -> None:
        if self.version == 0:
            self.root = {}  # 无效所有数据
        self.version = 1


@custom_data_chunk(identifier=DC_MACRO, include_json_object=True)
class _(DataChunkBase):
    def __init__(self):
        super().__init__()


@custom_data_chunk(identifier=DC_VARIABLE, include_json_object=True)
class _(DataChunkBase):
    def __init__(self):
        super().__init__()


@custom_data_chunk(identifier=DC_USER_DATA, include_json_object=True)
class _(DataChunkBase):
    def __init__(self):
        super().__init__()
        self.version: int = 0

    def introspect(self) -> None:
        if self.version == 0:
            self.root = {}  # 无效所有数据
        self.version = 1


@custom_data_chunk(identifier=DC_GROUP_DATA, include_json_object=True)
class _(DataChunkBase):
    def __init__(self):
        super().__init__()
        self.version: int = 0

    def introspect(self) -> None:
        if self.version == 0:
            self.root = {}  # 无效所有数据
        self.version = 1


@custom_data_chunk(identifier=DC_NICKNAME)
class _(DataChunkBase):
    def __init__(self):
        super().__init__()
