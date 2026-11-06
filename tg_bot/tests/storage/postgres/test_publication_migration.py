import asyncio
import importlib.util
from pathlib import Path

from aerich.utils import decompress_dict

MIGRATION = (
    Path(__file__).resolve().parents[3]
    / "migrations"
    / "models"
    / "2_20261105130000_knowledge_publication.py"
)


def load_migration():
    spec = importlib.util.spec_from_file_location("knowledge_publication_migration", MIGRATION)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_publication_migration_contains_pg_authority_and_ledger():
    migration = load_migration()
    sql = asyncio.run(migration.upgrade(None))

    assert migration.RUN_IN_TRANSACTION is True
    assert 'ADD COLUMN IF NOT EXISTS "active_generation_id"' in sql
    assert 'CREATE TABLE IF NOT EXISTS "knowledge_generations"' in sql
    assert 'CREATE TABLE IF NOT EXISTS "llm_attempts"' in sql
    assert '"attempt_id" VARCHAR(128) NOT NULL UNIQUE' in sql


def test_publication_migration_models_state_has_new_models():
    state = decompress_dict(load_migration().MODELS_STATE)

    assert "models.KnowledgeGeneration" in state
    assert "models.LlmAttempt" in state
    project_fields = state["models.Project"]["data_fields"]
    assert any(field.get("name") == "active_generation_id" for field in project_fields)
