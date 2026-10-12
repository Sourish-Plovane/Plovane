"""Fine-tuned retrieval embeddings: the bi-encoder.

This is the "later" model in the spec, and the ordering matters. Until there
are a few thousand accepted recommendations there is nothing to fine-tune on,
so retrieval runs on an off-the-shelf code embedding model and the effort goes
into ranking. Once the accept log is deep enough, fine-tuning the same encoder
as a bi-encoder raises recall@1000, which is the one number no ranking model
can repair: an entry retrieval never returned cannot be ranked first.

Architecture: the same pretrained encoder, mean pooling over tokens, L2
normalisation, trained with contrastive (InfoNCE) loss using in-batch
negatives.

Requires torch and transformers (``pip install 'plovane[neural]'``).
"""

from __future__ import annotations

from collections.abc import Sequence

import numpy as np
import torch
from torch import Tensor, nn

from plovane.config import EMBEDDING_DIM
from plovane.retrieval.embedding import entry_embedding_text
from plovane.schemas import Entry

DEFAULT_ENCODER = "jinaai/jina-embeddings-v2-base-code"
"""A starting candidate, not a conclusion. Pick the encoder by measuring
recall@1000 on your own gold set, and check it per language: the MVP set
includes C++, CUDA, HTML and CSS, which most code encoders under-represent."""


# Input: hidden_states (Tensor), attention_mask (Tensor)
# Output: Tensor
# Processing: Average token vectors over real tokens only.
# Changed by: Claude Code
# Date: 2026-10-12
def mean_pool(hidden_states: Tensor, attention_mask: Tensor) -> Tensor:
    """Average token vectors over real tokens only.

    Padding must be excluded explicitly. Pooling over the padded positions
    would make a short entry's vector depend on the longest entry in its
    batch, which silently changes its embedding between indexing runs.
    """
    mask = attention_mask.unsqueeze(-1).to(hidden_states.dtype)
    summed = (hidden_states * mask).sum(dim=1)
    counts = mask.sum(dim=1).clamp(min=1e-9)
    return summed / counts


class CodeBiEncoder(nn.Module):
    """Encoder + mean pooling + L2 normalisation, shared across both towers.

    One set of weights encodes queries and entries alike. Separate towers would
    double the parameters and, with only a few thousand training pairs, overfit
    well before they paid for themselves.
    """

    # Input: encoder_name (str), dimension (int), trust_remote_code (bool)
    # Output: None
    # Processing: Init
    # Changed by: Claude Code
    # Date: 2026-10-12
    def __init__(
        self,
        encoder_name: str = DEFAULT_ENCODER,
        *,
        dimension: int = EMBEDDING_DIM,
        trust_remote_code: bool = True,
    ) -> None:
        super().__init__()
        from transformers import AutoModel, AutoTokenizer

        self.encoder_name = encoder_name
        self.tokenizer = AutoTokenizer.from_pretrained(encoder_name, trust_remote_code=trust_remote_code)
        self.encoder = AutoModel.from_pretrained(encoder_name, trust_remote_code=trust_remote_code)

        hidden = int(self.encoder.config.hidden_size)
        self.dimension = dimension
        # Only project when the encoder's width is not the width pgvector holds;
        # an identity here keeps the pretrained space untouched.
        self.projection: nn.Module = nn.Identity() if hidden == dimension else nn.Linear(hidden, dimension)

    # Input: input_ids (Tensor), attention_mask (Tensor)
    # Output: Tensor
    # Processing: Forward
    # Changed by: Claude Code
    # Date: 2026-10-12
    def forward(self, input_ids: Tensor, attention_mask: Tensor) -> Tensor:
        output = self.encoder(input_ids=input_ids, attention_mask=attention_mask)
        pooled = mean_pool(output.last_hidden_state, attention_mask)
        return nn.functional.normalize(self.projection(pooled), p=2, dim=-1)

    # -- Inference ---------------------------------------------------------

    # Input: texts (Sequence[str]), max_length (int), batch_size (int)
    # Output: np.ndarray
    # Processing: Encode texts
    # Changed by: Claude Code
    # Date: 2026-10-12
    def _encode_texts(self, texts: Sequence[str], *, max_length: int, batch_size: int) -> np.ndarray:
        if not texts:
            return np.zeros((0, self.dimension), dtype=np.float32)

        device = next(self.parameters()).device
        was_training = self.training
        self.eval()
        chunks: list[np.ndarray] = []
        try:
            with torch.inference_mode():
                for start in range(0, len(texts), batch_size):
                    batch = list(texts[start : start + batch_size])
                    encoded = self.tokenizer(
                        batch,
                        padding=True,
                        truncation=True,
                        max_length=max_length,
                        return_tensors="pt",
                    ).to(device)
                    vectors = self(encoded["input_ids"], encoded["attention_mask"])
                    chunks.append(vectors.float().cpu().numpy())
        finally:
            self.train(was_training)
        return np.concatenate(chunks, axis=0).astype(np.float32)

    # Input: texts (Sequence[str]), max_length (int), batch_size (int)
    # Output: np.ndarray
    # Processing: Queries are short, so they get a tighter length budget than entries.
    # Changed by: Claude Code
    # Date: 2026-10-12
    def encode_queries(
        self, texts: Sequence[str], *, max_length: int = 256, batch_size: int = 32
    ) -> np.ndarray:
        """Queries are short, so they get a tighter length budget than entries."""
        return self._encode_texts(texts, max_length=max_length, batch_size=batch_size)

    # Input: entries (Sequence[Entry]), max_length (int), batch_size (int)
    # Output: np.ndarray
    # Processing: Encode entries
    # Changed by: Claude Code
    # Date: 2026-10-12
    def encode_entries(
        self, entries: Sequence[Entry], *, max_length: int = 512, batch_size: int = 16
    ) -> np.ndarray:
        return self._encode_texts(
            [entry_embedding_text(e) for e in entries], max_length=max_length, batch_size=batch_size
        )


# Input: query_vectors (Tensor), entry_vectors (Tensor), temperature (float)
# Output: Tensor
# Processing: Contrastive loss with in-batch negatives.
# Changed by: Claude Code
# Date: 2026-10-12
def info_nce_loss(
    query_vectors: Tensor,
    entry_vectors: Tensor,
    *,
    temperature: float = 0.05,
) -> Tensor:
    """Contrastive loss with in-batch negatives.

    Row ``i`` of ``query_vectors`` is paired with row ``i`` of
    ``entry_vectors``; every other entry in the batch is a negative. Both are
    already unit-length, so the similarity matrix is a plain dot product and
    the temperature sets how sharply near-misses are punished.

    In-batch negatives make the batch size the negative count, so a batch of 64
    gives 63 negatives per query for free. The catch is that they are *random*
    negatives, mostly easy ones; once recall plateaus, mine hard negatives from
    retrieval results the way the cross-encoder does.
    """
    if query_vectors.shape != entry_vectors.shape:
        raise ValueError(
            f"query and entry batches must align, got {tuple(query_vectors.shape)} and {tuple(entry_vectors.shape)}"
        )
    if temperature <= 0:
        raise ValueError(f"temperature must be positive, got {temperature}")

    logits = query_vectors @ entry_vectors.transpose(0, 1) / temperature
    targets = torch.arange(logits.size(0), device=logits.device)
    # Symmetric: queries pick entries and entries pick queries, which keeps the
    # space from collapsing to a direction that only works one way round.
    return 0.5 * (
        nn.functional.cross_entropy(logits, targets)
        + nn.functional.cross_entropy(logits.transpose(0, 1), targets)
    )
