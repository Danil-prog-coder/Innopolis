import pytest
from pydantic import ValidationError

from app.config import Settings

BASE_ENV = {
    "BOT_TOKEN": "1:bot-secret-token",
    "POSTGRES_HOST": "pg",
    "POSTGRES_PORT": "5432",
    "POSTGRES_DB": "bot",
    "POSTGRES_USER": "u",
    "POSTGRES_PASSWORD": "pg-secret-42",
    "REDIS_HOST": "r",
    "REDIS_PORT": "6379",
    "REDIS_DB": "0",
    "REDIS_PASSWORD": "redis-secret-42",
}


@pytest.fixture
def env(monkeypatch):
    for key, value in BASE_ENV.items():
        monkeypatch.setenv(key, value)
    return monkeypatch


def test_settings_build_dsns(env):
    settings = Settings(_env_file=None)
    assert settings.postgres_dsn == "postgres://u:pg-secret-42@pg:5432/bot"
    assert settings.redis_url == "redis://:redis-secret-42@r:6379/0"
    assert settings.bot_token.get_secret_value() == "1:bot-secret-token"


def test_settings_repr_hides_secrets(env):
    text = repr(Settings(_env_file=None))
    for secret in ("bot-secret-token", "pg-secret-42", "redis-secret-42"):
        assert secret not in text


def test_settings_escape_special_chars_in_passwords(env):
    env.setenv("POSTGRES_PASSWORD", "p@ss:w/rd")
    env.setenv("REDIS_PASSWORD", "a@b")
    settings = Settings(_env_file=None)
    assert settings.postgres_dsn == "postgres://u:p%40ss%3Aw%2Frd@pg:5432/bot"
    assert settings.redis_url == "redis://:a%40b@r:6379/0"


def test_settings_empty_redis_password(env):
    env.setenv("REDIS_PASSWORD", "")
    assert Settings(_env_file=None).redis_url == "redis://r:6379/0"


def test_settings_defaults(env):
    settings = Settings(_env_file=None)
    assert settings.password_attempts_limit == 5
    assert settings.password_attempts_window_seconds == 900
    assert settings.password_reveal_ttl_seconds == 600
    assert settings.docs_max_files == 10


def test_settings_missing_token_fails(env):
    env.delenv("BOT_TOKEN")
    with pytest.raises(ValidationError):
        Settings(_env_file=None)


def test_settings_schedule_defaults(env):
    settings = Settings(_env_file=None)
    assert (settings.scheduler_timezone, settings.digest_weekday, settings.digest_hour) == (
        "Europe/Moscow",
        0,
        10,
    )


@pytest.mark.parametrize(
    ("key", "value"),
    [("SCHEDULER_TIMEZONE", "Mars/Olympus"), ("DIGEST_WEEKDAY", "7"), ("DIGEST_HOUR", "24")],
)
def test_settings_reject_bad_schedule(env, key, value):
    env.setenv(key, value)
    with pytest.raises(ValidationError):
        Settings(_env_file=None)
