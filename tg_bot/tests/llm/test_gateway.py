import pytest

from app.core.enums import DocsMode
from app.llm.gateway import AskResult, DocFile, LLMGateway, StubLLMGateway


async def test_stub_never_answers():
    gateway = StubLLMGateway()
    assert await gateway.ask(1, "q") == AskResult(found=False, answer=None)
    assert await gateway.ask_image(1, b"img", None) == AskResult(found=False, answer=None)


@pytest.mark.parametrize(
    ("mode", "prefix"),
    [(DocsMode.APPEND, "Документация дополнена"), (DocsMode.REPLACE, "Документация заменена")],
)
async def test_stub_summary_lists_files(mode, prefix):
    files = [DocFile("a.md", b"x"), DocFile("b.pdf", b"y")]
    summary = await StubLLMGateway().upload_documents(1, mode, files)
    assert summary == f"{prefix}. Файлы: a.md, b.pdf"


async def test_stub_save_knowledge_and_close_are_noops():
    gateway = StubLLMGateway()
    assert await gateway.save_knowledge(1, "q", "a") is None
    assert await gateway.close() is None


def test_gateway_is_abstract():
    with pytest.raises(TypeError):
        LLMGateway()
