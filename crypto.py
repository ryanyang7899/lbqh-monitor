"""平台凭据加密：用 Fernet 对称加密存储用户填写的联并千行账号密码 / MAAS APIKey。

主密钥来源（按优先级）：
  1. 环境变量 MASTER_KEY（base64，长度 44）
  2. 数据库同目录的 master.key 文件
  3. 首次运行自动生成 master.key（建议 chmod 600 并离线备份）

丢失主密钥 = 无法解密已存凭据（用户需重新填写），请妥善备份 master.key。
"""
import os
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken

from config import cfg


def _key_path() -> Path:
    return Path(cfg.DB_PATH).parent / "master.key"


def _load_key() -> bytes:
    env = os.getenv("MASTER_KEY", "").strip()
    if env:
        return env.encode()
    kp = _key_path()
    if kp.exists():
        return kp.read_bytes().strip()
    key = Fernet.generate_key()
    kp.write_bytes(key)
    try:
        os.chmod(kp, 0o600)
    except OSError:
        pass
    return key


_fernet = Fernet(_load_key())


def encrypt(plaintext: str) -> str:
    """加密明文，返回 base64 密文；空串返回空串。"""
    if not plaintext:
        return ""
    return _fernet.encrypt(plaintext.encode("utf-8")).decode("ascii")


def decrypt(ciphertext: str) -> str:
    """解密密文；解析失败或空串返回空串（密钥轮换后旧密文会导致历史凭据丢失）。"""
    if not ciphertext:
        return ""
    try:
        return _fernet.decrypt(ciphertext.encode("ascii")).decode("utf-8")
    except (InvalidToken, ValueError):
        return ""


def encrypt_many(**kw) -> dict:
    return {k: encrypt(v) for k, v in kw.items()}