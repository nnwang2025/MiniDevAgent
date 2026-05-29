from __future__ import annotations
import os
from dataclasses import dataclass
from pathlib import Path
from dotenv import load_dotenv
import yaml

load_dotenv()

CONFIG_FILE = Path(__file__).resolve().parents[1] / "config" / "settings.yaml"

@dataclass
class Settings:
    openai_api_key: str | None
    openai_base_url: str | None
    model_name: str
    tool_only: bool
    default_port: int
    enable_graph: bool


def load_settings() -> Settings:
    defaults = {
        "model_name": "Qwen/Qwen2.5-32B-Instruct",
        "tool_only": False,
        "default_port": 8000,
        "enable_graph": True,
    }

    if CONFIG_FILE.exists():
        with open(CONFIG_FILE, "r", encoding="utf-8") as handle:
            raw = yaml.safe_load(handle) or {}
            defaults.update(raw)

    openai_api_key = os.getenv("OPENAI_API_KEY")
    openai_base_url = os.getenv("OPENAI_BASE_URL")
    model_name = os.getenv("MODEL_NAME", defaults["model_name"])
    tool_only = os.getenv("TOOL_ONLY", str(defaults["tool_only"])) in ["1", "true", "True"]
    default_port = int(os.getenv("APP_PORT", defaults["default_port"]))
    enable_graph = os.getenv("ENABLE_GRAPH", str(defaults["enable_graph"])) in ["1", "true", "True"]

    if not openai_api_key:
        tool_only = True

    return Settings(
        openai_api_key=openai_api_key,
        openai_base_url=openai_base_url,
        model_name=model_name,
        tool_only=tool_only,
        default_port=default_port,
        enable_graph=enable_graph,
    )
