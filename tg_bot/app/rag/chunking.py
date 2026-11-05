"""TXT/Markdown ingestion: decode, structural sections, token-aware chunks.

Контракт Source/KnowledgeSnapshot переиспользуется из ``app.llm.gateway``.
Модуль не публикует generation и не пишет в PostgreSQL. Chunk text — точная
подстрока исходного документа; токенизатор проверяет полный input с префиксом
``passage: `` и special tokens, молчаливая обрезка запрещена.
"""

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from app.llm.gateway import DocFile

MAX_DOCUMENT_BYTES = 20 * 1024 * 1024
MAX_DECODED_CHARS = 2_000_000
PARSER_VERSION = "markdown-sections-v1"
CHUNKER_VERSION = "token-structural-v1"
PASSAGE_PREFIX = "passage: "
QUERY_PREFIX = "query: "
MAX_INPUT_TOKENS = 512
TARGET_TOKENS = 400
OVERLAP_TOKENS = 50


class RagError(RuntimeError):
    """Базовая ошибка ingestion/retrieval."""


class UnsupportedDocumentError(RagError):
    pass


class EmptyDocumentError(RagError):
    pass


class CorruptDocumentError(RagError):
    pass


class DocumentTooLargeError(RagError):
    pass


class ChunkingError(RagError):
    pass


class DocumentAlreadyExistsError(RagError):
    pass


class GenerationAlreadyExistsError(RagError):
    pass


class EmptyCorpusError(RagError):
    pass


class Tokenizer(Protocol):
    def encode(self, text: str, add_special_tokens: bool = True) -> list[int]: ...


@dataclass(frozen=True, slots=True)
class LoadedDocument:
    filename: str
    text: str
    document_id: str
    document_version: str
    title: str


@dataclass(frozen=True, slots=True)
class Chunk:
    chunk_id: str
    document_id: str
    text: str
    title: str
    section_path: str | None
    start_char: int
    end_char: int
    ordinal: int


@dataclass(frozen=True, slots=True)
class _Section:
    start: int
    end: int
    path: str | None


@dataclass(frozen=True, slots=True)
class _Unit:
    start: int
    end: int


def _clean_display(value: str, *, fallback: str) -> str:
    text = " ".join(value.split())
    if re.search(r"[A-Za-z][A-Za-z0-9+.-]*://|www\.|t\.me/", text, re.I):
        return fallback
    if re.search(r"(^|\s)(?:/[^\s]|~[\\/]|[A-Za-z]:[\\/]|\\\\)", text):
        return fallback
    text = re.sub(r"[^\w\s.,:;!?/«»→—–'\"-]", " ", text, flags=re.UNICODE)
    text = " ".join(text.replace("_", " ").split()).strip(" -")
    return text[:160] or fallback


_HEADING_RE = re.compile(r"^ {0,3}(#{1,6})[ \t]+(.+?)[ \t]*$")
_FENCE_OPEN_RE = re.compile(r"^ {0,3}(`{3,}|~{3,})")
_FENCE_CLOSE_RE = re.compile(r"^ {0,3}(`{3,}|~{3,})[ \t]*$")


def _heading_matches(text: str) -> list[tuple[int, int, str]]:
    """Headings outside fenced blocks only; ``` and ~~~ fences are preserved."""
    headings: list[tuple[int, int, str]] = []
    in_fence = False
    fence_char = ""
    fence_len = 0
    offset = 0
    for raw_line in text.splitlines(keepends=True):
        line = raw_line.rstrip("\r\n")
        if in_fence:
            closing = _FENCE_CLOSE_RE.match(line)
            if (
                closing is not None
                and closing.group(1)[0] == fence_char
                and len(closing.group(1)) >= fence_len
            ):
                in_fence = False
            offset += len(raw_line)
            continue
        opening = _FENCE_OPEN_RE.match(line)
        if opening is not None:
            in_fence = True
            fence_char = opening.group(1)[0]
            fence_len = len(opening.group(1))
            offset += len(raw_line)
            continue
        heading = _HEADING_RE.match(line)
        if heading is not None:
            headings.append((offset, len(heading.group(1)), heading.group(2).strip()))
        offset += len(raw_line)
    return headings


def _first_heading(text: str) -> str | None:
    headings = _heading_matches(text)
    return headings[0][2] if headings else None


def load_document(
    file: DocFile,
    *,
    max_bytes: int = MAX_DOCUMENT_BYTES,
    max_chars: int = MAX_DECODED_CHARS,
) -> LoadedDocument:
    filename = Path(file.filename).name
    suffix = Path(filename).suffix.lower()
    if suffix not in {".md", ".txt"}:
        raise UnsupportedDocumentError(f"поддерживаются только .md/.txt: {filename}")
    if len(file.content) > max_bytes:
        raise DocumentTooLargeError(f"{filename}: больше {max_bytes} байт")
    try:
        text = file.content.decode("utf-8")
    except UnicodeDecodeError as error:
        raise CorruptDocumentError(f"{filename}: не UTF-8") from error
    text = text.lstrip("\ufeff")
    if not text.strip():
        raise EmptyDocumentError(f"{filename}: пустой документ")
    if len(text) > max_chars:
        raise DocumentTooLargeError(f"{filename}: больше {max_chars} символов")
    document_version = hashlib.sha256(text.encode()).hexdigest()
    document_id = hashlib.sha256(f"{filename}\0{document_version}".encode()).hexdigest()[:20]
    title = _clean_display(
        _first_heading(text) or Path(filename).stem,
        fallback=Path(filename).stem or "Источник",
    )
    return LoadedDocument(
        filename=filename,
        text=text,
        document_id=document_id,
        document_version=document_version,
        title=title,
    )


def count_input_tokens(tokenizer: Tokenizer, text: str, prefix: str = PASSAGE_PREFIX) -> int:
    ids = tokenizer.encode(prefix + text, add_special_tokens=True)
    return len(ids)


def _sections(text: str) -> list[_Section]:
    headings = _heading_matches(text)
    if not headings:
        return [_Section(0, len(text), None)]

    sections: list[_Section] = []
    stack: list[tuple[int, str]] = []
    cursor = 0
    for start, level, title in headings:
        if start > cursor and text[cursor:start].strip():
            sections.append(_Section(cursor, start, _path(stack)))
        while stack and stack[-1][0] >= level:
            stack.pop()
        stack.append((level, title))
        cursor = start
    if cursor < len(text) and text[cursor:].strip():
        sections.append(_Section(cursor, len(text), _path(stack)))
    return sections


def _path(stack: list[tuple[int, str]]) -> str | None:
    if not stack:
        return None
    return " → ".join(title for _, title in stack)


def _has_body(section_text: str) -> bool:
    in_fence = False
    fence_char = ""
    fence_len = 0
    for raw_line in section_text.splitlines(keepends=True):
        line = raw_line.rstrip("\r\n")
        if in_fence:
            closing = _FENCE_CLOSE_RE.match(line)
            if (
                closing is not None
                and closing.group(1)[0] == fence_char
                and len(closing.group(1)) >= fence_len
            ):
                in_fence = False
            elif line.strip():
                return True
            continue
        opening = _FENCE_OPEN_RE.match(line)
        if opening is not None:
            # Fenced code is body even if it contains '#' comment lines.
            return True
        if _HEADING_RE.match(line) is not None:
            continue
        if line.strip():
            return True
    return False


def _split_long(
    text: str, start: int, end: int, tokenizer: Tokenizer, *, target_tokens: int
) -> list[_Unit]:
    units: list[_Unit] = []
    cursor = start
    while cursor < end:
        low, high = cursor + 1, end
        best = cursor + 1
        while low <= high:
            middle = (low + high) // 2
            if count_input_tokens(tokenizer, text[cursor:middle]) <= target_tokens:
                best = middle
                low = middle + 1
            else:
                high = middle - 1
        # Не разрезаем слово, если рядом есть пробел; подстрока остаётся точной.
        cut = best
        space = text.rfind(" ", cursor, best + 1)
        if space > cursor:
            cut = space
        if cut <= cursor:
            cut = best
        units.append(_Unit(cursor, cut))
        cursor = cut
        while cursor < end and text[cursor] == " ":
            cursor += 1
    return units


def _paragraph_units(
    text: str, start: int, end: int, tokenizer: Tokenizer, *, target_tokens: int
) -> list[_Unit]:
    units: list[_Unit] = []
    for match in re.finditer(r"\S(?:.*?\S)?(?=\n\s*\n|\Z)", text[start:end], re.S):
        unit_start = start + match.start()
        unit_end = start + match.end()
        if count_input_tokens(tokenizer, text[unit_start:unit_end]) <= target_tokens:
            units.append(_Unit(unit_start, unit_end))
            continue
        sentences = list(
            re.finditer(
                r"\S(?:.*?\S)?(?<=[.!?…])(?=\s|\Z)",
                text[unit_start:unit_end],
                re.S,
            )
        )
        if not sentences:
            units.extend(
                _split_long(
                    text,
                    unit_start,
                    unit_end,
                    tokenizer,
                    target_tokens=target_tokens,
                )
            )
            continue
        for sentence in sentences:
            sentence_start = unit_start + sentence.start()
            sentence_end = unit_start + sentence.end()
            if count_input_tokens(tokenizer, text[sentence_start:sentence_end]) <= target_tokens:
                units.append(_Unit(sentence_start, sentence_end))
            else:
                units.extend(
                    _split_long(
                        text,
                        sentence_start,
                        sentence_end,
                        tokenizer,
                        target_tokens=target_tokens,
                    )
                )
    return units


def chunk_document(
    document: LoadedDocument,
    tokenizer: Tokenizer,
    *,
    target_tokens: int = TARGET_TOKENS,
    overlap_tokens: int = OVERLAP_TOKENS,
    max_input_tokens: int = MAX_INPUT_TOKENS,
) -> list[Chunk]:
    if not 0 < target_tokens < max_input_tokens:
        raise ChunkingError("target_tokens должен быть в (0, max_input_tokens)")
    if not 0 <= overlap_tokens < target_tokens:
        raise ChunkingError("overlap_tokens должен быть < target_tokens")

    chunks: list[Chunk] = []
    ordinal = 0
    for section in _sections(document.text):
        section_text = document.text[section.start : section.end]
        if not section_text.strip() or not _has_body(section_text):
            continue
        units = _paragraph_units(
            document.text,
            section.start,
            section.end,
            tokenizer,
            target_tokens=target_tokens,
        )
        if not units:
            continue
        start_index = 0
        while start_index < len(units):
            end_index = start_index
            while end_index < len(units):
                candidate = document.text[units[start_index].start : units[end_index].end]
                if count_input_tokens(tokenizer, candidate) > target_tokens:
                    break
                end_index += 1
            if end_index == start_index:
                end_index = start_index + 1
            start = units[start_index].start
            end = units[end_index - 1].end
            text = document.text[start:end]
            if count_input_tokens(tokenizer, text) > max_input_tokens:
                raise ChunkingError("чанк превышает 512 токенов")
            chunk_id = hashlib.sha256(
                f"{document.document_id}:{ordinal}:{start}:{end}:{text}".encode()
            ).hexdigest()
            chunks.append(
                Chunk(
                    chunk_id=chunk_id,
                    document_id=document.document_id,
                    text=text,
                    title=document.title,
                    section_path=section.path,
                    start_char=start,
                    end_char=end,
                    ordinal=ordinal,
                )
            )
            ordinal += 1
            if end_index >= len(units):
                break
            overlap_start = end_index
            for candidate_start in range(end_index - 1, start_index, -1):
                overlap_text = document.text[units[candidate_start].start : end]
                if count_input_tokens(tokenizer, overlap_text) <= overlap_tokens:
                    overlap_start = candidate_start
            start_index = overlap_start if overlap_start > start_index else end_index
    return chunks
