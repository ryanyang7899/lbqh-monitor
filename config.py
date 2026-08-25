"""配置加载：从 .env 环境变量读取，避免硬编码敏感信息。"""
import os
from pathlib import Path

from dotenv import load_dotenv

# 加载项目根目录下的 .env（不存在也不报错）
load_dotenv(Path(__file__).parent / ".env")


def _get(key: str, default: str = "") -> str:
    return os.getenv(key, default).strip()


class Config:
    # 平台账号
    LBQH_USER = _get("LBQH_USER")
    LBQH_PASSWORD = _get("LBQH_PASSWORD")
    LBQH_BASE_URL = _get("LBQH_BASE_URL", "https://lbqh.paratera.com")

    # MaaS 模型 API（OpenAI 兼容）
    MAAS_API_URL = _get("MAAS_API_URL", "https://maasapi.paratera.com/v1/chat/completions")
    MAAS_API_KEY = _get("MAAS_API_KEY")
    CAPTCHA_MODEL = _get("CAPTCHA_MODEL", "Qwen3.5-35B-A3B")

    # 抓取间隔（秒）
    FETCH_INTERVAL = int(_get("FETCH_INTERVAL", "3600"))

    # 对外接口鉴权 Token
    API_TOKEN = _get("API_TOKEN", "lbqh-2026-token")

    # SQLite 数据库文件
    DB_PATH = _get("DB_PATH", "balance.db")


cfg = Config()
