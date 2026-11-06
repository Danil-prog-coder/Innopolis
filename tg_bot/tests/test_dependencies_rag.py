import pytest
from pydantic import SecretStr

from app import dependencies


def test_real_mode_without_llm_config_fails_closed(settings, monkeypatch):
    real = settings.model_copy(
        update={
            "rag_mode": "real",
            "llm_base_url": "",
            "llm_model": "",
            "llm_api_key": SecretStr(""),
        }
    )
    monkeypatch.setattr(dependencies, "get_obj_settings", lambda: real)
    dependencies.get_obj_llm_gateway.cache_clear()
    try:
        with pytest.raises(dependencies.RealModeConfigurationError, match="required"):
            dependencies.get_obj_llm_gateway()
    finally:
        dependencies.get_obj_llm_gateway.cache_clear()


def test_stub_mode_does_not_construct_real_stack(settings, monkeypatch):
    stub = settings.model_copy(update={"rag_mode": "stub"})
    monkeypatch.setattr(dependencies, "get_obj_settings", lambda: stub)
    dependencies.get_obj_llm_gateway.cache_clear()
    try:
        gateway = dependencies.get_obj_llm_gateway()
        assert gateway.__class__.__name__ == "StubLLMGateway"
    finally:
        dependencies.get_obj_llm_gateway.cache_clear()
