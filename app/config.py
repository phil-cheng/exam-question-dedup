"""读写 exe 旁的 config.json。试题不落盘，这里只存服务配置。"""

from __future__ import annotations

import json
import sys
from dataclasses import asdict, dataclass
from pathlib import Path


def app_dir() -> Path:
    """配置文件所在目录：打包后是 exe 旁，开发时是项目根。"""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def resource_dir() -> Path:
    """只读内置资源：打包后在解压目录，开发时在项目根。"""
    if getattr(sys, "frozen", False):
        return Path(sys._MEIPASS)  # type: ignore[attr-defined]
    return Path(__file__).resolve().parent.parent


def template_path() -> Path:
    return resource_dir() / "template.xls"


@dataclass
class AppConfig:
    embed_base_url: str = ""
    embed_model: str = ""
    embed_api_key: str = ""

    @property
    def embed_enabled(self) -> bool:
        return bool(self.embed_base_url.strip() and self.embed_model.strip())


def config_path() -> Path:
    return app_dir() / "config.json"


def load_config() -> AppConfig:
    path = config_path()
    if not path.is_file():
        return AppConfig()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return AppConfig()
    if not isinstance(raw, dict):
        return AppConfig()
    cfg = AppConfig()
    cfg.embed_base_url = str(raw.get("embed_base_url", "") or "")
    cfg.embed_model = str(raw.get("embed_model", "") or "")
    cfg.embed_api_key = str(raw.get("embed_api_key", "") or "")
    return cfg


def save_config(cfg: AppConfig) -> None:
    path = config_path()
    path.write_text(
        json.dumps(asdict(cfg), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
