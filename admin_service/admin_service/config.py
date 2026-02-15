"""Config reader for admin service — reads the shared YAML config file."""

import os
from dataclasses import dataclass, field, fields
from typing import Any


@dataclass
class AdminServiceConfig:
    """Minimal config the admin service needs."""
    config_path: str = "config.yaml"
    redis_url: str = "redis://localhost:6379/0"
    admin_password: str = ""


def load_yaml(path: str) -> dict:
    """Load YAML file and return as dict."""
    import yaml
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def save_yaml(path: str, data: dict) -> None:
    """Write dict to YAML file."""
    import yaml
    with open(path, "w", encoding="utf-8") as f:
        yaml.dump(data, f, default_flow_style=False, sort_keys=False)


def get_admin_config() -> AdminServiceConfig:
    """Build admin service config from environment."""
    config = AdminServiceConfig()
    config.config_path = os.getenv("CONFIG_PATH", "config.yaml")

    # Try to read Redis URL and admin password from the YAML config
    try:
        data = load_yaml(config.config_path)
        cache_section = data.get("cache", {})
        if redis_url := cache_section.get("redis_url"):
            config.redis_url = redis_url
        admin_section = data.get("admin", {})
        if password := admin_section.get("password"):
            config.admin_password = password
    except FileNotFoundError:
        pass

    # Env vars override
    if redis_url := os.getenv("REDIS_URL"):
        config.redis_url = redis_url

    return config
