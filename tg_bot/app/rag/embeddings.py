"""Pinned E5-small encoder with the contract's 512-token budget."""

import logging

import numpy as np

from app.rag.chunking import (
    MAX_INPUT_TOKENS,
    PASSAGE_PREFIX,
    QUERY_PREFIX,
    Tokenizer,
    count_input_tokens,
)

logger = logging.getLogger(__name__)

MODEL_NAME = "intfloat/multilingual-e5-small"
MODEL_REVISION = "614241f622f53c4eeff9890bdc4f31cfecc418b3"
EMBEDDING_DIMENSION = 384


class EmbeddingError(RuntimeError):
    pass


class E5Embedder:
    """Real local baseline; не заменяется fake в integration-проверках."""

    def __init__(
        self,
        *,
        model_name: str = MODEL_NAME,
        revision: str = MODEL_REVISION,
        device: str = "cpu",
        batch_size: int = 16,
    ) -> None:
        from sentence_transformers import SentenceTransformer

        self.model_name = model_name
        self.revision = revision
        self.batch_size = batch_size
        logger.info("Loading embedding model %s@%s on %s", model_name, revision, device)
        self.model = SentenceTransformer(model_name, revision=revision, device=device)
        self.model.max_seq_length = MAX_INPUT_TOKENS
        self._tokenizer: Tokenizer = self.model.tokenizer

    @property
    def tokenizer(self) -> Tokenizer:
        return self._tokenizer

    @property
    def query_prefix(self) -> str:
        return QUERY_PREFIX

    @property
    def passage_prefix(self) -> str:
        return PASSAGE_PREFIX

    @property
    def metric(self) -> str:
        return "cosine"

    @property
    def normalization(self) -> str:
        return "l2"

    @property
    def max_input_tokens(self) -> int:
        return MAX_INPUT_TOKENS

    @property
    def dimension(self) -> int:
        getter = getattr(self.model, "get_embedding_dimension", None)
        return int(
            getter() if getter is not None else self.model.get_sentence_embedding_dimension()
        )

    def _encode(self, texts: list[str], prefix: str) -> np.ndarray:
        if not texts:
            return np.empty((0, EMBEDDING_DIMENSION), dtype=np.float32)
        for text in texts:
            tokens = count_input_tokens(self._tokenizer, text, prefix)
            if tokens > MAX_INPUT_TOKENS:
                raise EmbeddingError(
                    f"input {tokens} tokens exceeds {MAX_INPUT_TOKENS}; no silent truncation"
                )
        vectors = self.model.encode(
            [prefix + text for text in texts],
            batch_size=self.batch_size,
            normalize_embeddings=True,
            show_progress_bar=False,
            convert_to_numpy=True,
        )
        array = np.asarray(vectors, dtype=np.float32)
        if array.ndim != 2 or array.shape[1] != self.dimension:
            raise EmbeddingError(f"unexpected embedding shape {array.shape}")
        return array

    def embed_passages(self, texts: list[str]) -> np.ndarray:
        return self._encode(texts, PASSAGE_PREFIX)

    def embed_query(self, question: str) -> np.ndarray:
        if not question.strip():
            raise EmbeddingError("пустой вопрос")
        vectors = self._encode([question], QUERY_PREFIX)
        return vectors[0]
