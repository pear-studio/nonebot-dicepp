from typing import Union
from io import BytesIO
from pathlib import Path
from base64 import b64encode


def get_cq_image(file: Union[str, bytes, BytesIO, Path]) -> str:
    if isinstance(file, BytesIO):
        file = file.getvalue()
    if isinstance(file, bytes):
        file = f"base64://{b64encode(file).decode()}"
    elif isinstance(file, Path):
        file = f"file:///{file.resolve()}"
    elif isinstance(file, str):
        file = f"file:///{file}"
    return f"[CQ:image,file={file}]"

def get_cq_reply(message_id: str) -> str:
    if message_id.isdigit():
        return f"[CQ:reply,id={message_id}]\n"
    else:
        return ""

def get_cq_at(user_id: str) ->str:
    if user_id.isdigit():
        return f"[CQ:at,qq={user_id}]"
    else:
        return f"@{user_id}"