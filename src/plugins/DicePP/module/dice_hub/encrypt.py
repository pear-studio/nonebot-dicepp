import os
from typing import Tuple
import rsa
import base64
import zlib

RSA_LEN = 1024
ENCRYPT_SEG_LEN = RSA_LEN // 8
CONTENT_SEG_LEN = ENCRYPT_SEG_LEN - 11

HEADER_LEN = 1
MAX_TEXT_LEN = 2 ** (8 * HEADER_LEN) * CONTENT_SEG_LEN - 1

BYTE_TO_INT_RULE = {"byteorder": "big", "signed": False}
INT_TO_BYTE_RULE = {"length": HEADER_LEN, **BYTE_TO_INT_RULE}

ENCODE_STYLE = "utf-8"


def encrypt_rsa(text: str, public_key: rsa.PublicKey) -> str:
    byte_data = text.encode(ENCODE_STYLE)
    byte_data = zlib.compress(byte_data)
    assert len(byte_data) < MAX_TEXT_LEN
    seg_num = len(byte_data) // CONTENT_SEG_LEN + 1
    header = (seg_num - 1).to_bytes(**INT_TO_BYTE_RULE)
    result = header
    for seg_index in range(seg_num):
        seg_data = byte_data[seg_index * CONTENT_SEG_LEN:(seg_index + 1) * CONTENT_SEG_LEN]
        result += rsa.encrypt(seg_data, public_key)
    result = base64.b64encode(result)
    return result.decode(ENCODE_STYLE)


def decrypt_rsa(rsa_str: str, private_key: rsa.PrivateKey) -> str:
    decode_data = rsa_str.encode(ENCODE_STYLE)
    decode_data = base64.b64decode(decode_data)
    header, decode_data = decode_data[:HEADER_LEN], decode_data[HEADER_LEN:]
    seg_num = int.from_bytes(header, **BYTE_TO_INT_RULE) + 1
    result = b""
    for seg_index in range(seg_num):
        seg_data = decode_data[seg_index * ENCRYPT_SEG_LEN:(seg_index + 1) * ENCRYPT_SEG_LEN]
        try:
            result += rsa.decrypt(seg_data, private_key)
        except rsa.pkcs1.DecryptionError:
            raise ValueError("RSA Fail")
    try:
        result = zlib.decompress(result)
    except zlib.error:
        raise ValueError("Z-lib Error")
    return result.decode(ENCODE_STYLE)


def create_rsa_key(name: str, path: str) -> Tuple[rsa.PublicKey, rsa.PrivateKey]:
    public_key, private_key = rsa.newkeys(RSA_LEN)
    try:
        save_rsa_public_key(public_key, name, path)
        save_rsa_private_key(private_key, name, path)
    except PermissionError as e:
        raise e
    return public_key, private_key


def save_rsa_public_key(public_key: rsa.PublicKey, name: str, path: str) -> str:
    public_path = os.path.join(path, name) + ".pub"
    try:
        with open(public_path, "w") as f:
            f.write(save_rsa_public_key_as_str(public_key))
    except PermissionError as e:
        raise e
    return public_path


def save_rsa_private_key(private_key: rsa.PrivateKey, name: str, path: str) -> str:
    private_path = os.path.join(path, name)
    try:
        with open(private_path, "w") as f:
            f.write(save_rsa_private_key_as_str(private_key))
    except PermissionError as e:
        raise e
    return private_path


def load_rsa_public_key(name: str, path: str) -> rsa.PublicKey:
    public_path = os.path.join(path, name) + ".pub"
    try:
        with open(public_path, "r") as f:
            key_str = f.read()
    except FileNotFoundError:
        raise ValueError()
    return load_rsa_public_key_from_str(key_str)


def load_rsa_private_key(name: str, path: str) -> rsa.PrivateKey:
    private_path = os.path.join(path, name)
    try:
        with open(private_path, "r") as f:
            key_str = f.read()
    except FileNotFoundError:
        raise ValueError()
    return load_rsa_private_key_from_str(key_str)


def load_rsa_public_key_from_str(key_str: str) -> rsa.PublicKey:
    return rsa.PublicKey.load_pkcs1(key_str.encode(ENCODE_STYLE))


def load_rsa_private_key_from_str(key_str: str) -> rsa.PrivateKey:
    return rsa.PrivateKey.load_pkcs1(key_str.encode(ENCODE_STYLE))


def save_rsa_public_key_as_str(public_key: rsa.PublicKey) -> str:
    return public_key.save_pkcs1().decode(ENCODE_STYLE)


def save_rsa_private_key_as_str(private_key: rsa.PrivateKey) -> str:
    return private_key.save_pkcs1().decode(ENCODE_STYLE)
