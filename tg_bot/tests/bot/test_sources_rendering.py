import pytest
from aiogram.methods import SendRichMessage

from app.bot import views
from app.llm.gateway import AskResult, KnowledgeSnapshot, Source
from tests.bot.conftest import USER_TG

GROUP = -100500


@pytest.fixture
async def user_in_project(project_service, project, user):
    await project_service.switch_project(user, project.id)
    return user


def snapshot_for(project) -> KnowledgeSnapshot:
    return KnowledgeSnapshot.compat(project.id, project.docs_version)


def source_for(snapshot: KnowledgeSnapshot) -> Source:
    return Source(
        project_id=snapshot.project_id,
        generation_id=snapshot.generation_id,
        knowledge_version=snapshot.knowledge_version,
        document_id="internal-doc-path",
        document_version="v1",
        chunk_id="internal-chunk-id",
        title="Настройка доступа",
        excerpt="Фрагмент",
        citation_label="[1]",
        section_path="Профиль → Доступ",
        page=12,
    )


def test_with_sources_shows_only_public_metadata():
    snapshot = KnowledgeSnapshot(7, "generation-internal", 3)
    text = views.with_sources("Ответ", (source_for(snapshot),))

    assert "Ответ" in text
    assert "Настройка доступа" in text
    assert "Профиль → Доступ" in text
    assert "стр. 12" in text
    assert "internal-doc-path" not in text
    assert "internal-chunk-id" not in text
    assert "generation-internal" not in text


def test_with_sources_neutralizes_markdown_and_html():
    source = Source(
        project_id=7,
        generation_id="generation-internal",
        knowledge_version=3,
        document_id="internal-doc",
        document_version="1",
        chunk_id="internal-chunk",
        title="**<tg-button>[x] evil**",
        excerpt="x",
        citation_label="[1]",
        section_path="<b>/Users/secret</b> [x](https://evil)",
    )

    text = views.with_sources("Ответ", (source,))

    assert "<" not in text
    assert ">" not in text
    assert "[" not in text.split("**Источники:**", 1)[0]
    assert "](https://evil)" not in text
    assert "https://" not in text
    assert "/Users/secret" not in text
    assert "**<tg-button>" not in text


def test_source_title_rejects_paths_and_urls():
    for title in (
        "https://evil",
        "/Users/secret",
        "/etc/passwd",
        "/tmp/secret",
        "C:\\secret",
        "\\\\server\\share",
        "~/secret",
        "file:///tmp/x",
        "tg://user?id=1",
        "[x](tg://user?id=1)",
    ):
        with pytest.raises(ValueError, match="display text"):
            Source(
                project_id=7,
                generation_id="g",
                knowledge_version=1,
                document_id="d",
                document_version="1",
                chunk_id="c",
                title=title,
                excerpt="x",
                citation_label="[1]",
            )


def test_with_sources_rejects_mutated_or_duplicate_labels():
    class BadSource:
        citation_label = "[evil2]"
        title = "Title"
        section_path = None
        page = None

    with pytest.raises(views.LLMContractError):
        views.with_sources("A", (BadSource(),))  # type: ignore[arg-type]

    source = source_for(KnowledgeSnapshot(7, "g", 1))
    with pytest.raises(views.LLMContractError):
        views.with_sources("A", (source, source))


def test_with_sources_blocks_general_absolute_paths():
    for path in ("/etc/passwd", "/tmp/secret", "C:\\secret", "\\\\srv\\share", "~/secret"):
        source = Source(
            project_id=7,
            generation_id="g",
            knowledge_version=1,
            document_id="d",
            document_version="1",
            chunk_id="c",
            title="Раздел",
            excerpt="x",
            citation_label="[1]",
            section_path=path,
        )
        text = views.with_sources("A", (source,))
        assert path not in text
        assert "Раздел" in text


def test_with_sources_blocks_tg_and_encoded_markdown_metadata():
    source = Source(
        project_id=7,
        generation_id="g",
        knowledge_version=1,
        document_id="d",
        document_version="1",
        chunk_id="c",
        title="Раздел",
        excerpt="x",
        citation_label="[1]",
        section_path="См. [x](tg://user?id=1) и tg%3A%2F%2Fuser%3Fid%3D1",
    )

    text = views.with_sources("A", (source,))

    assert "tg://" not in text
    assert "](tg" not in text
    assert "tg%3A" not in text
    assert "Раздел" in text


def test_with_sources_keeps_normal_cyrillic_metadata():
    snapshot = KnowledgeSnapshot(7, "g", 1)
    text = views.with_sources("Ответ", (source_for(snapshot),))

    assert "Настройка доступа" in text
    assert "Профиль → Доступ" in text
    assert "стр. 12" in text


def test_without_sources_answer_is_unchanged():
    assert views.with_sources("Ответ", ()) == "Ответ"


async def test_private_answer_renders_sources_on_cache_miss_and_hit(
    feed, session, fake_llm, user_in_project, project
):
    snapshot = snapshot_for(project)
    fake_llm.answer = AskResult(
        found=True,
        answer="Ответ",
        sources=(source_for(snapshot),),
        snapshot=snapshot,
    )

    await feed.message("q", from_user=USER_TG)
    first = session.last(SendRichMessage).rich_message.markdown

    assert "Ответ" in first
    assert "Настройка доступа" in first
    assert "Профиль → Доступ" in first
    assert "стр. 12" in first
    assert "internal-doc-path" not in first
    assert "internal-chunk-id" not in first
    assert session.last(SendRichMessage).rich_message.skip_entity_detection is True

    session.clear()
    await feed.message("q", from_user=USER_TG)
    second = session.last(SendRichMessage).rich_message.markdown

    assert second == first
    assert fake_llm.ask_calls == 1


async def test_group_answer_renders_sources(feed, session, fake_llm, project_password, project):
    snapshot = snapshot_for(project)
    fake_llm.answer = AskResult(
        found=True,
        answer="Ответ",
        sources=(source_for(snapshot),),
        snapshot=snapshot,
    )

    await feed.message(f"/join@test_bot {project_password}", chat_type="supergroup", chat_id=GROUP)
    await feed.message("/ask@test_bot q", chat_type="supergroup", chat_id=GROUP)

    sent = session.last(SendRichMessage)
    assert sent.chat_id == GROUP
    assert sent.rich_message.skip_entity_detection is True
    assert "Настройка доступа" in sent.rich_message.markdown
    assert "стр. 12" in sent.rich_message.markdown
