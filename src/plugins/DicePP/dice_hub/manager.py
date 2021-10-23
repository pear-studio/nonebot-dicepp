import os
import base64
from typing import Dict
import rsa

from bot_config import KEY_PATH

from dice_hub.encrypt import encrypt_rsa, decrypt_rsa, create_rsa_key, ENCODE_STYLE
from dice_hub.encrypt import load_rsa_public_key, load_rsa_private_key, save_rsa_public_key, save_rsa_private_key
from dice_hub.encrypt import load_rsa_public_key_from_str, load_rsa_private_key_from_str, save_rsa_public_key_as_str, save_rsa_private_key_as_str

MSG_SEP = "$%*" * 2


class HubManager:
    def __init__(self, identifier: str):
        self.identifier = identifier
        self.public_key: rsa.PublicKey
        self.private_key: rsa.PrivateKey
        # 加载自己的秘钥
        try:
            self.public_key = load_rsa_public_key(identifier, KEY_PATH)
            self.private_key = load_rsa_private_key(identifier, KEY_PATH)
        except ValueError:
            try:
                self.public_key, self.private_key = create_rsa_key(identifier, KEY_PATH)
            except PermissionError as e:
                raise e
        # 加载其他人的秘钥
        self.public_key_dict: Dict[str, rsa.PublicKey] = {identifier: self.public_key}
        file_list = os.listdir(KEY_PATH)
        for file_name in file_list:
            if file_name.endswith(".pub"):
                pub_id = file_name[:-4]
                try:
                    self.public_key_dict[pub_id] = load_rsa_public_key(pub_id, KEY_PATH)
                except ValueError:
                    pass

    def encrypt_msg(self, msg: str, target_id: str):
        """target_id对应的秘钥不存在将会抛出KeyError"""
        if target_id not in self.public_key_dict:
            raise KeyError(f"{target_id}秘钥不存在")
        return encrypt_rsa(msg, self.public_key_dict[target_id])

    def decrypt_msg(self, msg: str):
        return decrypt_rsa(msg, self.private_key)

    def record_public_key(self, public_key_str: str, target_id: str):
        try:
            self.public_key_dict[target_id] = load_rsa_public_key_from_str(public_key_str)
        except ValueError:
            raise ValueError("秘钥格式不正确")
        try:
            save_rsa_public_key(self.public_key_dict[target_id], target_id, KEY_PATH)
        except PermissionError as e:
            raise e
        return

    def is_valid(self, target_id: str) -> bool:
        return target_id in self.public_key_dict.keys()

    def generate_card(self) -> str:
        """生成个人名片"""
        public_str = save_rsa_public_key_as_str(self.public_key)
        card = f"{self.identifier}{MSG_SEP}{public_str}"
        # 象征性混淆一下
        card = base64.b64encode(card.encode(ENCODE_STYLE)).decode(ENCODE_STYLE)
        return card

    def record_card(self, card: str):
        """读取对方名片"""
        card = base64.b64decode(card.encode(ENCODE_STYLE)).decode(ENCODE_STYLE)
        info = card.split(MSG_SEP, 1)
        if len(info) != 2:
            raise ValueError("名片格式不正确: 分隔符不存在")
        target_id: str
        public_str: str
        target_id, public_str = info
        self.record_public_key(public_str, target_id)
