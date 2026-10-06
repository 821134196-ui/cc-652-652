"""全局配置：路径全部可通过环境变量覆盖，便于测试隔离。"""
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

DATA_DIR = Path(os.environ.get("APP_DATA_DIR", BASE_DIR / "data"))
DB_PATH = Path(os.environ.get("APP_DB_PATH", DATA_DIR / "consent.db"))
FILES_DIR = Path(os.environ.get("APP_FILES_DIR", DATA_DIR / "files"))
STATIC_DIR = Path(os.environ.get("APP_STATIC_DIR", BASE_DIR / "static"))


def ensure_dirs() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    FILES_DIR.mkdir(parents=True, exist_ok=True)
