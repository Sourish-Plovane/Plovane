"""Stage 2: the cross-encoder. 100 candidates, scored precisely.

The cross-encoder reads the query and the candidate *together*, so every query
token can attend to every code token. That is far more accurate than comparing
two independently computed embeddings, and it costs a forward pass per
candidate -- affordable on 100 items, impossible on the whole library. This is
why retrieval and Stage 1 exist: to get the list down to where this model can
be used.

Architecture, per the spec:

===============================  ===========  ======================================
Layer                            Output       Notes
===============================  ===========  ======================================
Token + position embeddings      512 x 768    From the pretrained encoder
Transformer encoder, 12 layers   512 x 768    Pretrained code model; all layers tuned
[CLS] hidden state               768          Summary of the query-candidate pair
Dropout                          768          p = 0.1
Linear                           256          768 to 256
GELU                             256          Activation
Dropout                          256          p = 0.1
Linear                           1            Relevance logit
===============================  ===========  ======================================

Requires torch and transformers (``pip install 'plovane[neural]'``).
"""

from __future__ import annotations

import math
import warnings
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

import numpy as np
import torch
from torch import Tensor, nn

from ml.config import (
    CROSS_ENCODER_MAX_TOKENS,
    HARD_NEGATIVES_PER_GROUP,
    MVP_LANGUAGES,
    TrainingConfig,
)
from ml.schemas import Candidate, Entry

DEFAULT_ENCODER = "microsoft/unixcoder-base"

UNIXCODER_PRETRAINING_LANGUAGES: frozenset[str] = frozenset(
    {"java", "ruby", "python", "php", "javascript", "go"}
)
"""What UniXcoder was actually pretrained on.

Which leaves out C++, CUDA, HTML and CSS -- four of the six MVP languages. The
model still tokenises them, so nothing *fails*; it just ranks them worse, and
the overall average hides it. Before training, either pick an encoder whose
model card lists pretraining data covering every MVP language, or confirm on
the gold set that UniXcoder still ranks well in the missing ones. Report
NDCG@10 per language either way.
"""


# Input: encoder_pretraining_languages (Iterable[str]), mvp_languages (Sequence[str])
# Output: tuple[str, ...]
# Processing: Return the MVP languages the encoder was not pretrained on, and warn.
# Changed by: Claude Code
# Date: 2026-10-12
def check_language_coverage(
    encoder_pretraining_languages: Iterable[str],
    *,
    mvp_languages: Sequence[str] = MVP_LANGUAGES,
) -> tuple[str, ...]:
    """Return the MVP languages the encoder was not pretrained on, and warn.

    Called at model construction so the gap is on the record at training time
    rather than discovered in a per-language evaluation months later.
    """
    covered = {lang.lower() for lang in encoder_pretraining_languages}
    missing = tuple(lang for lang in mvp_languages if lang.lower() not in covered)
    if missing:
        warnings.warn(
            f"encoder was not pretrained on {', '.join(missing)}; validate NDCG@10 "
            "per language on the gold set before shipping, or choose another encoder",
            stacklevel=2,
        )
    return missing


# Input: language (str)
# Output: str
# Processing: ``<lang:cuda>`` and friends.
# Changed by: Claude Code
# Date: 2026-10-12
def language_token(language: str) -> str:
    """``<lang:cuda>`` and friends.

    Prepended to every input so the model knows which language it is scoring.
    Without it the model has to infer the language from syntax, which is
    ambiguous exactly where it hurts most -- CUDA C++ reads as C++, and a
    helper written for one is wrong in the other.
    """
    return f"<lang:{language.strip().lower()}>"


# Input: entry (Entry)
# Output: tuple[str, str]
# Processing: The candidate side, split into the part that must survive truncation
# Changed by: Claude Code
# Date: 2026-10-12
def entry_text(entry: Entry) -> tuple[str, str]:
    """The candidate side, split into the part that must survive truncation
    and the part that gets cut first.

    Returns ``(head, code)``: the head is name, signature and docstring -- what
    the entry *claims* to do -- and the code is the evidence. The code is
    truncated first because a long implementation would otherwise push the
    signature out of the window, leaving the model scoring an unlabelled blob.
    """
    head_parts = [entry.name, entry.signature or "", entry.docstring, entry.description]
    return "\n".join(p for p in head_parts if p), entry.code


@dataclass(frozen=True)
class EncodedPair:
    input_ids: list[int]
    attention_mask: list[int]


class CrossEncoderTokenizer:
    """Builds ``[CLS] query + project context [SEP] candidate [SEP]``.

    Assembles the pair piece by piece rather than handing both strings to the
    tokenizer's own truncation, because the spec's truncation order -- code
    first -- is not one of the strategies the tokenizer offers.
    """

    # Input: tokenizer, max_tokens (int), query_max_tokens (int)
    # Output: None
    # Processing: Init
    # Changed by: Claude Code
    # Date: 2026-10-12
    def __init__(self, tokenizer, *, max_tokens: int = CROSS_ENCODER_MAX_TOKENS, query_max_tokens: int = 192) -> None:
        self.tokenizer = tokenizer
        self.max_tokens = max_tokens
        self.query_max_tokens = query_max_tokens

        self.cls_id = tokenizer.cls_token_id
        self.sep_id = tokenizer.sep_token_id
        self.pad_id = tokenizer.pad_token_id if tokenizer.pad_token_id is not None else 0
        if self.cls_id is None or self.sep_id is None:
            raise ValueError(f"tokenizer {tokenizer!r} has no [CLS]/[SEP]; the pair format needs both")

    # Input: text (str)
    # Output: list[int]
    # Processing: Ids
    # Changed by: Claude Code
    # Date: 2026-10-12
    def _ids(self, text: str) -> list[int]:
        if not text:
            return []
        return self.tokenizer(text, add_special_tokens=False)["input_ids"]

    # Input: query_text (str), entry (Entry)
    # Output: EncodedPair
    # Processing: Encode
    # Changed by: Claude Code
    # Date: 2026-10-12
    def encode(self, query_text: str, entry: Entry) -> EncodedPair:
        # [CLS] ... [SEP] ... [SEP]
        budget = self.max_tokens - 3
        if budget <= 0:
            raise ValueError(f"max_tokens={self.max_tokens} leaves no room for content")

        query_ids = self._ids(f"{language_token(entry.language)} {query_text}")
        query_ids = query_ids[: min(self.query_max_tokens, budget // 2)]

        head, code = entry_text(entry)
        entry_budget = budget - len(query_ids)
        head_ids = self._ids(head)[:entry_budget]
        code_ids = self._ids(code)[: max(0, entry_budget - len(head_ids))]

        input_ids = [self.cls_id, *query_ids, self.sep_id, *head_ids, *code_ids, self.sep_id]
        return EncodedPair(input_ids=input_ids, attention_mask=[1] * len(input_ids))

    # Input: pairs (Sequence[EncodedPair])
    # Output: dict[str, Tensor]
    # Processing: Pad a batch to its longest member, not to 512.
    # Changed by: Claude Code
    # Date: 2026-10-12
    def collate(self, pairs: Sequence[EncodedPair]) -> dict[str, Tensor]:
        """Pad a batch to its longest member, not to 512.

        Most pairs are far shorter than the window, so padding to the batch
        maximum is the difference between scoring 100 candidates in one pass
        and timing out.
        """
        if not pairs:
            raise ValueError("nothing to collate")
        width = max(len(p.input_ids) for p in pairs)
        input_ids = torch.full((len(pairs), width), self.pad_id, dtype=torch.long)
        attention_mask = torch.zeros((len(pairs), width), dtype=torch.long)
        for row, pair in enumerate(pairs):
            n = len(pair.input_ids)
            input_ids[row, :n] = torch.tensor(pair.input_ids, dtype=torch.long)
            attention_mask[row, :n] = 1
        return {"input_ids": input_ids, "attention_mask": attention_mask}


class CrossEncoder(nn.Module):
    """Pretrained code encoder plus the relevance head from the layer table."""

    tokenizer_class: type[CrossEncoderTokenizer] = CrossEncoderTokenizer
    """Which input format to build pairs with.

    A specialisation overrides this rather than rebuilding the model. The spec
    keeps separate rankers for templates and helpers, and they differ only in
    how the candidate side is laid out -- a template has no signature to show
    -- so the format is the part worth varying.
    """

    extra_special_tokens: tuple[str, ...] = ()

    # Input: encoder_name (str), hidden_dropout (float), head_dim (int), max_tokens (int), pretraining_languages (Iterable[str]), check_languages (bool), tokenizer, encoder
    # Output: None
    # Processing: Build the model, downloading the pretrained encoder by name.
    # Changed by: Claude Code
    # Date: 2026-10-12
    def __init__(
        self,
        encoder_name: str = DEFAULT_ENCODER,
        *,
        hidden_dropout: float = 0.1,
        head_dim: int = 256,
        max_tokens: int = CROSS_ENCODER_MAX_TOKENS,
        pretraining_languages: Iterable[str] = UNIXCODER_PRETRAINING_LANGUAGES,
        check_languages: bool = True,
        tokenizer=None,
        encoder=None,
    ) -> None:
        """Build the model, downloading the pretrained encoder by name.

        ``tokenizer`` and ``encoder`` override the download with objects you
        already hold. That is what makes the input format, the truncation
        order and the loss testable without pulling half a gigabyte of
        pretrained weights, and it is the only way to exercise this class
        where the Hugging Face hub is unreachable. Pass both or neither.
        """
        super().__init__()

        if (tokenizer is None) != (encoder is None):
            raise ValueError("pass both tokenizer and encoder, or neither")

        if check_languages:
            self.missing_languages = check_language_coverage(pretraining_languages)
        else:
            self.missing_languages = ()

        self.encoder_name = encoder_name
        self.max_tokens = max_tokens

        if tokenizer is None:
            from transformers import AutoTokenizer

            base_tokenizer = AutoTokenizer.from_pretrained(encoder_name)
        else:
            base_tokenizer = tokenizer

        # The language tokens are added to the vocabulary rather than left to
        # split into pieces, so <lang:cuda> is one unambiguous signal.
        added = base_tokenizer.add_tokens(
            [*(language_token(lang) for lang in MVP_LANGUAGES), *self.extra_special_tokens],
            special_tokens=True,
        )
        self.tokenizer = self.tokenizer_class(base_tokenizer, max_tokens=max_tokens)

        if encoder is None:
            from transformers import AutoModel

            self.encoder = AutoModel.from_pretrained(encoder_name)  # 12 layers, all fine-tuned
        else:
            self.encoder = encoder
        if added:
            self.encoder.resize_token_embeddings(len(base_tokenizer))

        hidden = int(self.encoder.config.hidden_size)
        self.head = nn.Sequential(
            nn.Dropout(hidden_dropout),
            nn.Linear(hidden, head_dim),
            nn.GELU(),
            nn.Dropout(hidden_dropout),
            nn.Linear(head_dim, 1),
        )

    # Input: input_ids (Tensor), attention_mask (Tensor)
    # Output: Tensor
    # Processing: Return one relevance logit per row.
    # Changed by: Claude Code
    # Date: 2026-10-12
    def forward(self, input_ids: Tensor, attention_mask: Tensor) -> Tensor:
        """Return one relevance logit per row.

        Deliberately *not* squashed to a probability here. Stage 3 consumes
        this as a feature, and calibration happens once, at the end, with
        isotonic regression -- a sigmoid at this point would be an uncalibrated
        number that merely looks like a probability.
        """
        output = self.encoder(input_ids=input_ids, attention_mask=attention_mask)
        cls_hidden = output.last_hidden_state[:, 0]  # [CLS] summarises the pair
        return self.head(cls_hidden).squeeze(-1)

    # -- Inference ---------------------------------------------------------

    # Input: query_text (str), candidates (Sequence[Candidate]), batch_size (int)
    # Output: np.ndarray
    # Processing: Score one query against its candidates, writing scores back on them.
    # Changed by: Claude Code
    # Date: 2026-10-12
    @torch.inference_mode()
    def score_candidates(
        self,
        query_text: str,
        candidates: Sequence[Candidate],
        *,
        batch_size: int = 16,
    ) -> np.ndarray:
        """Score one query against its candidates, writing scores back on them."""
        if not candidates:
            return np.zeros(0, dtype=np.float32)

        device = next(self.parameters()).device
        was_training = self.training
        self.eval()
        scores: list[np.ndarray] = []
        try:
            for start in range(0, len(candidates), batch_size):
                batch = candidates[start : start + batch_size]
                encoded = self.tokenizer.collate([self.tokenizer.encode(query_text, c.entry) for c in batch])
                logits = self(encoded["input_ids"].to(device), encoded["attention_mask"].to(device))
                scores.append(logits.float().cpu().numpy())
        finally:
            self.train(was_training)

        flat = np.concatenate(scores).astype(np.float32)
        for candidate, score in zip(candidates, flat, strict=True):
            candidate.cross_encoder_score = float(score)
        return flat


# -- Training ---------------------------------------------------------------


@dataclass
class CrossEncoderGroup:
    """One query, one correct entry, and its hard negatives.

    The positive is always at index 0, which is what makes the listwise loss a
    one-liner and the label implicit rather than something to keep in sync.
    """

    query_text: str
    positive: Entry
    negatives: tuple[Entry, ...]

    # Input: none
    # Output: tuple[Entry, ...]
    # Processing: Entries
    # Changed by: Claude Code
    # Date: 2026-10-12
    @property
    def entries(self) -> tuple[Entry, ...]:
        return (self.positive, *self.negatives)


# Input: retrieved (Sequence[Candidate]), positive_ids (Sequence[str]), count (int)
# Output: list[Entry]
# Processing: Wrong entries that retrieval ranked highly.
# Changed by: Claude Code
# Date: 2026-10-12
def mine_hard_negatives(
    retrieved: Sequence[Candidate],
    positive_ids: Sequence[str],
    *,
    count: int = HARD_NEGATIVES_PER_GROUP,
) -> list[Entry]:
    """Wrong entries that retrieval ranked highly.

    Hard negatives are the whole point of this training setup. Random
    negatives teach the model to tell a CUDA kernel from a CSS rule, which it
    already knows; the mistakes that matter in production are close
    look-alikes -- ``random_split`` offered where ``stratified_split`` was
    wanted -- and those only appear near the top of the retrieval list.
    """
    positives = set(positive_ids)
    return [c.entry for c in retrieved if c.id not in positives][:count]


# Input: logits (Tensor), positive_index (int)
# Output: Tensor
# Processing: Softmax cross-entropy over the group.
# Changed by: Claude Code
# Date: 2026-10-12
def listwise_softmax_loss(logits: Tensor, *, positive_index: int = 0) -> Tensor:
    """Softmax cross-entropy over the group.

    Listwise, not pairwise: the loss is computed across the whole group at
    once, so the gradient pushes the correct entry above *all* of its
    look-alikes simultaneously rather than one pair at a time. That is what
    teaches the ranking rather than a per-item relevance score.

    ``logits`` is ``(group_size,)`` or ``(batch, group_size)``.
    """
    if logits.ndim == 1:
        logits = logits.unsqueeze(0)
    if logits.ndim != 2:
        raise ValueError(f"expected (batch, group_size) logits, got {tuple(logits.shape)}")
    targets = torch.full((logits.size(0),), positive_index, dtype=torch.long, device=logits.device)
    return nn.functional.cross_entropy(logits, targets)


# Input: optimizer, total_steps (int), warmup_fraction (float)
# Output: unannotated
# Processing: Linear warmup over the first ``warmup_fraction`` of steps, then linear decay.
# Changed by: Claude Code
# Date: 2026-10-12
def linear_warmup_schedule(optimizer, *, total_steps: int, warmup_fraction: float):
    """Linear warmup over the first ``warmup_fraction`` of steps, then linear decay.

    Warmup matters more than usual here: the head is randomly initialised while
    the encoder is pretrained, so the first few steps would otherwise push large
    gradients from an untrained head straight through 12 tuned layers.
    """
    if total_steps <= 0:
        raise ValueError(f"total_steps must be positive, got {total_steps}")
    warmup_steps = max(1, int(total_steps * warmup_fraction))

    # Input: step (int)
    # Output: float
    # Processing: Lr lambda
    # Changed by: Claude Code
    # Date: 2026-10-12
    def lr_lambda(step: int) -> float:
        if step < warmup_steps:
            return step / warmup_steps
        progress = (step - warmup_steps) / max(1, total_steps - warmup_steps)
        return max(0.0, 1.0 - progress)

    return torch.optim.lr_scheduler.LambdaLR(optimizer, lr_lambda)


# Input: model (CrossEncoder), groups (Sequence[CrossEncoderGroup]), config (TrainingConfig | None), device (str | torch.device | None)
# Output: list[float]
# Processing: Fine-tune all layers with the spec's optimiser settings.
# Changed by: Claude Code
# Date: 2026-10-12
def train_cross_encoder(
    model: CrossEncoder,
    groups: Sequence[CrossEncoderGroup],
    *,
    config: TrainingConfig | None = None,
    device: str | torch.device | None = None,
) -> list[float]:
    """Fine-tune all layers with the spec's optimiser settings.

    Returns the per-step loss history. The settings are starting values --
    tune them on the validation set, and remember that nothing here decides
    whether the result ships: the release gate in :mod:`ml.evaluation`
    does.
    """
    config = config or TrainingConfig()
    if not groups:
        raise ValueError("no training groups given")

    device = torch.device(device or ("cuda" if torch.cuda.is_available() else "cpu"))
    model.to(device)
    model.train()

    optimizer = torch.optim.AdamW(
        model.parameters(), lr=config.learning_rate, weight_decay=config.weight_decay
    )
    steps_per_epoch = math.ceil(len(groups) / config.batch_groups)
    scheduler = linear_warmup_schedule(
        optimizer,
        total_steps=steps_per_epoch * config.epochs,
        warmup_fraction=config.warmup_fraction,
    )

    use_bf16 = config.bf16 and device.type == "cuda" and torch.cuda.is_bf16_supported()
    history: list[float] = []

    for _ in range(config.epochs):
        for start in range(0, len(groups), config.batch_groups):
            batch = groups[start : start + config.batch_groups]
            optimizer.zero_grad(set_to_none=True)
            loss = accumulate_batch_gradients(model, batch, device=device, bf16=use_bf16)
            torch.nn.utils.clip_grad_norm_(model.parameters(), config.max_grad_norm)
            optimizer.step()
            scheduler.step()
            history.append(loss)

    return history


# Input: model (CrossEncoder), batch (Sequence[CrossEncoderGroup]), device (torch.device), bf16 (bool)
# Output: float
# Processing: Leave the batch-mean loss gradient in .grad, one group alive at a time.
# Changed by: Claude Code
# Date: 2026-10-15
def accumulate_batch_gradients(
    model: CrossEncoder,
    batch: Sequence[CrossEncoderGroup],
    *,
    device: torch.device,
    bf16: bool = False,
) -> float:
    """Leave the gradient of the batch-mean loss in ``.grad``; return that loss.

    Backward runs once per group, each loss scaled by ``1 / len(batch)``. The
    batch loss is a mean, and the gradient of a mean is the mean of the
    gradients, so the accumulated gradient is the same one a single backward
    over the stacked losses would give -- but only one group's activations are
    ever alive at once, so memory no longer grows with ``batch_groups``.

    Stacking first is what the loop used to do, and it does not fit. Eight
    groups of eight pairs at 256 tokens reached 14 GB on CPU and was killed by
    the kernel; the GPU defaults -- sixteen pairs at 512 tokens -- hold roughly
    four times as many tokens, which is past a 24 GB card even in bf16.

    Groups no longer need padding to a common width, either: each group's
    softmax is computed on its own, so there is nothing to align.
    """
    total = 0.0
    for group in batch:
        with torch.autocast(device_type=device.type, dtype=torch.bfloat16, enabled=bf16):
            pairs = [model.tokenizer.encode(group.query_text, e) for e in group.entries]
            encoded = model.tokenizer.collate(pairs)
            logits = model(encoded["input_ids"].to(device), encoded["attention_mask"].to(device))
            loss = listwise_softmax_loss(logits.unsqueeze(0)) / len(batch)
        loss.backward()
        total += float(loss.detach().cpu())
    return total
