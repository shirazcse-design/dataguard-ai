"""Feature construction: word + character TF-IDF over content, plus a filename block.

No rule outputs and no embedded labels are used as features (see docs/uc4/ml-plan.md).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

import numpy as np
from scipy.sparse import csr_matrix, hstack
from sklearn.feature_extraction.text import TfidfVectorizer

from app.classification.schemas import Document

from .config import FeatureConfig

_SPLIT = re.compile(r"[^A-Za-z0-9]+")


def filename_text(document: Document) -> str:
    """Filename tokens plus the extension as a distinct token (`ext_py`)."""
    tokens = [t.lower() for t in _SPLIT.split(document.filename) if t]
    if document.extension:
        tokens.append(f"ext_{document.extension.lower().lstrip('.')}")
    return " ".join(tokens)


def content_text(document: Document, max_chars: int) -> str:
    return document.content[:max_chars]


@dataclass
class FeatureBuilder:
    cfg: FeatureConfig
    seed: int = 0
    word: TfidfVectorizer | None = field(default=None, repr=False)
    char: TfidfVectorizer | None = field(default=None, repr=False)
    name: TfidfVectorizer | None = field(default=None, repr=False)
    blocks: dict[str, tuple[int, int]] = field(default_factory=dict)

    def _make(self):
        c = self.cfg
        word = TfidfVectorizer(
            analyzer="word",
            ngram_range=tuple(c.word_ngram_range),
            min_df=c.word_min_df,
            max_features=c.word_max_features,
            sublinear_tf=c.sublinear_tf,
            lowercase=True,
            dtype=np.float64,
        )
        char = TfidfVectorizer(
            analyzer="char_wb",
            ngram_range=tuple(c.char_ngram_range),
            min_df=c.char_min_df,
            max_features=c.char_max_features,
            sublinear_tf=c.sublinear_tf,
            lowercase=True,
            dtype=np.float64,
        )
        name = TfidfVectorizer(
            analyzer="word", token_pattern=r"\S+", min_df=1, sublinear_tf=True, dtype=np.float64
        )
        return word, char, name

    def fit(self, documents: list[Document]) -> FeatureBuilder:
        self.word, self.char, self.name = self._make()
        texts = [content_text(d, self.cfg.max_chars) for d in documents]
        blocks = [self.word.fit_transform(texts), self.char.fit_transform(texts)]
        if self.cfg.filename_block:
            blocks.append(self.name.fit_transform([filename_text(d) for d in documents]))
        self._record_blocks(blocks)
        return self

    def _record_blocks(self, blocks: list[csr_matrix]) -> None:
        start, out = 0, {}
        for label, m in zip(["word", "char", "filename"], blocks, strict=False):
            out[label] = (start, start + m.shape[1])
            start += m.shape[1]
        self.blocks = out

    def transform(self, documents: list[Document]) -> csr_matrix:
        texts = [content_text(d, self.cfg.max_chars) for d in documents]
        blocks = [self.word.transform(texts), self.char.transform(texts)]
        if self.cfg.filename_block:
            blocks.append(self.name.transform([filename_text(d) for d in documents]))
        return hstack(blocks, format="csr")

    def word_feature_names(self) -> np.ndarray:
        return self.word.get_feature_names_out()

    @property
    def n_features(self) -> int:
        return max(end for _, end in self.blocks.values()) if self.blocks else 0
