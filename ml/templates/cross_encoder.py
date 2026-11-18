"""The template cross-encoder: Stage 2 for templates.

The spec keeps separate cross-encoders for templates and helpers, or one model
with a type token prepended, and says to pick whichever scores higher on the
evaluation set. This is the separate-model arm; the type token is here anyway,
so the same class trains the single-model arm by pointing it at both types.
Run both and let the gold set decide -- that comparison is the experiment, and
nothing in this module settles it.

What actually differs from the helper format:

**No signature.** The spec leaves ``signature`` out of a template's card
because a template is not called. Keeping the field in the model input would
feed it an empty slot on every example.

**The code is the description.** A helper's docstring usually says what it
does and the body is detail; a template's name and blurb are thin and the
scaffold itself carries the meaning. So the candidate side reserves a floor
for code, and the truncation keeps the *head* of the file -- the imports,
top-level declarations and structure, which are what identify a scaffold. The
tail of a template is boilerplate, which is the right thing to lose.

Requires torch and transformers (``pip install 'plovane[neural]'``).
"""

from __future__ import annotations

from collections.abc import Sequence

from ml.config import CROSS_ENCODER_MAX_TOKENS
from ml.ranking.cross_encoder import (
    CrossEncoder,
    CrossEncoderTokenizer,
    EncodedPair,
    language_token,
)
from ml.schemas import Entry, EntryType

TEMPLATE_TYPE_TOKENS: tuple[str, ...] = ("<type:template>", "<type:bundle>", "<type:helper>")


# Input: entry (Entry)
# Output: str
# Processing: ``<type:template>`` and friends, so one model can serve both arms.
# Changed by: Sourish
# Date: 2026-10-12
def type_token(entry: Entry) -> str:
    """``<type:template>`` and friends, so one model can serve both arms."""
    return f"<type:{entry.type.value}>"


# Input: entry (Entry)
# Output: str
# Processing: The candidate side's non-code half: name and description, no signature.
# Changed by: Sourish
# Date: 2026-10-12
def template_head(entry: Entry) -> str:
    """The candidate side's non-code half: name and description, no signature."""
    parts = [entry.name, entry.description, entry.docstring]
    return "\n".join(p for p in parts if p)


class TemplateCrossEncoderTokenizer(CrossEncoderTokenizer):
    """``[CLS] query + context [SEP] <type> <lang> name + blurb + code [SEP]``.

    Reserves ``min_code_tokens`` for the scaffold before the head is allowed
    to spend the budget. Without that floor a template with a long README-style
    description would be scored on its own marketing copy, which is exactly the
    text least likely to distinguish it from nine near-identical starters.
    """

    # Input: tokenizer, max_tokens (int), query_max_tokens (int), min_code_tokens (int), head_max_tokens (int)
    # Output: None
    # Processing: Init
    # Changed by: Sourish
    # Date: 2026-10-12
    def __init__(
        self,
        tokenizer,
        *,
        max_tokens: int = CROSS_ENCODER_MAX_TOKENS,
        query_max_tokens: int = 160,
        min_code_tokens: int = 192,
        head_max_tokens: int = 96,
    ) -> None:
        super().__init__(tokenizer, max_tokens=max_tokens, query_max_tokens=query_max_tokens)
        self.min_code_tokens = min_code_tokens
        self.head_max_tokens = head_max_tokens

    # Input: query_text (str), entry (Entry)
    # Output: EncodedPair
    # Processing: Encode
    # Changed by: Sourish
    # Date: 2026-10-12
    def encode(self, query_text: str, entry: Entry) -> EncodedPair:
        budget = self.max_tokens - 3  # [CLS] ... [SEP] ... [SEP]
        if budget <= 0:
            raise ValueError(f"max_tokens={self.max_tokens} leaves no room for content")

        query_ids = self._ids(query_text)[: min(self.query_max_tokens, budget // 2)]
        entry_budget = budget - len(query_ids)

        markers = self._ids(f"{type_token(entry)} {language_token(entry.language)}")
        markers = markers[:entry_budget]
        entry_budget -= len(markers)

        # The head is capped, and capped again by whatever the code floor
        # leaves, so a verbose blurb cannot crowd the scaffold out.
        head_cap = min(self.head_max_tokens, max(0, entry_budget - self.min_code_tokens))
        head_ids = self._ids(template_head(entry))[:head_cap]
        code_ids = self._ids(entry.code)[: max(0, entry_budget - len(head_ids))]

        input_ids = [self.cls_id, *query_ids, self.sep_id, *markers, *head_ids, *code_ids, self.sep_id]
        return EncodedPair(input_ids=input_ids, attention_mask=[1] * len(input_ids))


class TemplateCrossEncoder(CrossEncoder):
    """The relevance head from the spec's layer table, on the template format.

    Identical architecture to the helper model -- token and position
    embeddings, 12 pretrained encoder layers all fine-tuned, the [CLS] hidden
    state through dropout, 768 to 256, GELU, dropout, 256 to 1. Only the input
    format and the training population change, which is what makes the
    separate-models-versus-one-model comparison a fair one.
    """

    tokenizer_class = TemplateCrossEncoderTokenizer
    extra_special_tokens = TEMPLATE_TYPE_TOKENS


# Input: entries (Sequence[Entry]), include_bundles (bool)
# Output: list[Entry]
# Processing: Filter a library down to what the template model is trained to score.
# Changed by: Sourish
# Date: 2026-10-12
def template_entries(entries: Sequence[Entry], *, include_bundles: bool = True) -> list[Entry]:
    """Filter a library down to what the template model is trained to score."""
    allowed = {EntryType.TEMPLATE}
    if include_bundles:
        allowed.add(EntryType.BUNDLE)
    return [e for e in entries if e.type in allowed]
