"""Конфигурация Tortoise ORM. ``TORTOISE_ORM`` читает aerich ([tool.aerich] в pyproject.toml)."""

from typing import Any

from app.config import Settings

MODELS_MODULES: dict[str, list[str]] = {
    "models": ["app.storage.postgres.models", "aerich.models"],
}


def build_tortoise_config(settings: Settings) -> dict[str, Any]:
    return {
        "connections": {"default": settings.postgres_dsn},
        "apps": {
            "models": {
                "models": MODELS_MODULES["models"],
                "default_connection": "default",
            }
        },
        "use_tz": True,
        "timezone": "UTC",
    }


def __getattr__(name: str) -> Any:
    # Лениво: импорт модуля не должен требовать заполненного .env
    if name == "TORTOISE_ORM":
        return build_tortoise_config(Settings())
    raise AttributeError(name)
