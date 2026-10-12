"""Synthetic training queries, and a hard block on evaluating with them.

The cross-encoder needs training groups, hand-labelled queries are scarce, and
generating queries from library entries is a standard way to get volume. It is
also a trap, and the trap is the reason this module exists in the shape it
does.

A query generated from an entry's description makes that entry the correct
answer **by construction**. Measuring NDCG@10 against it reports paraphrase
matching as relevance, and it reports it as an excellent score, because the
task really is to match a sentence to the sentence it came from. A release gate
reading that number would admit changes that improve paraphrase matching and
reject changes that improve what users actually ask for -- worse than no gate,
because the figure looks real.

Nothing about that failure announces itself. The metric is computed correctly,
the data is well-formed, nothing raises. So the restriction lives in the type
that builds a gold query, not in this docstring: every generated query carries
:data:`QUERY_PROVENANCE_SYNTHETIC`, and ``GoldQuery`` refuses it.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from ml.schemas import Entry

QUERY_PROVENANCE_SYNTHETIC = "synthetic"
"""Marker that bars a query from any evaluation set.

Checked by ``GoldQuery.__post_init__``, so the guard sits where the object is
constructed rather than where somebody remembers to look.
"""

# Two template sets, because an action phrase is either a verb phrase or a
# noun phrase and they do not take the same frames. "how do I a deep copy of
# an object" is what one set applied to both produces.
_VERB_TEMPLATES = (
    "how do I {action}",
    "need something to {action}",
    "is there a helper to {action}",
    "{action} in {language}",
)
_NOUN_TEMPLATES = (
    "{action}",
    "looking for {action}",
    "{action} in {language}",
    "is there something for {action}",
)

# Docstrings conventionally use the third person ("Splits a dataframe"), which
# does not fit a request frame, so a leading third-person verb is reduced to
# its base form.
_THIRD_PERSON = re.compile(r"^(\w+?)(?:es|s)$")
_NOUN_LEADERS = frozenset({"a", "an", "the", "deep", "copy", "list", "set", "map", "number"})


@dataclass(frozen=True)
class SyntheticQuery:
    """A generated query and the entry it was generated from."""

    query: str
    entry_id: str
    language: str
    source: str = QUERY_PROVENANCE_SYNTHETIC
    generator: str = "templated"

    # Input: none
    # Output: dict[str, object]
    # Processing: Flat record for a training-data file
    # Changed by: Claude Code
    # Date: 2026-10-12
    def as_dict(self) -> dict[str, object]:
        return {
            "query": self.query,
            "entry_id": self.entry_id,
            "language": self.language,
            "source": self.source,
            "generator": self.generator,
        }


@runtime_checkable
class QueryGenerator(Protocol):
    """Produces training queries for a library entry."""

    name: str

    def generate(self, entry: Entry, *, count: int = 3) -> list[SyntheticQuery]: ...


# Input: entry (Entry)
# Output: tuple[str, bool]
# Processing: An action phrase and whether it is a verb phrase
# Changed by: Claude Code
# Date: 2026-10-12
def action_phrase(entry: Entry) -> tuple[str, bool]:
    """Return ``(phrase, is_verb_phrase)`` describing what the entry does.

    Prefers the description, since an author's sentence says more than an
    identifier, and falls back to the name split into words.

    The verb flag is what keeps the output grammatical. Docstrings are written
    in the third person -- "Splits a dataframe" -- which does not fit a request
    frame, so a leading third-person verb is reduced to its base form. And a
    description beginning "Returns a deep copy" is a noun phrase once the verb
    is stripped, which cannot take a verb frame at all: applying one produces
    "how do I a deep copy of an object".
    """
    text = (entry.description or "").strip()
    if not text:
        from ml.ingest.description import words_from_identifier

        text = words_from_identifier(entry.name)
    text = text.rstrip(".").strip()
    if not text:
        return "", False

    for prefix in ("Returns ", "Return ", "Builds ", "Build ", "Creates ", "Create "):
        if text.startswith(prefix):
            # What follows is the thing produced, so a noun phrase.
            rest = text[len(prefix):].strip()
            return (rest[0].lower() + rest[1:], False) if rest else ("", False)

    for prefix in ("A ", "An ", "The "):
        if text.startswith(prefix):
            return text[0].lower() + text[1:], False

    head, _, tail = text.partition(" ")
    lowered = head.lower()
    if lowered in _NOUN_LEADERS:
        return text[0].lower() + text[1:], False

    match = _THIRD_PERSON.match(lowered)
    if match:
        base = match.group(1)
        return (f"{base} {tail}".strip(), True)
    return text[0].lower() + text[1:], True


class TemplatedQueryGenerator:
    """Deterministic paraphrases of an entry's description. No credential.

    Weaker than a model and worth having anyway: free, reproducible, and
    enough to shake out the training pipeline before money is spent on
    generation. Template choice is seeded by entry id, so the same entry gets
    the same queries on every run and a training set is reproducible.
    """

    name = "templated"

    # Input: entry (Entry), count (int)
    # Output: list[SyntheticQuery]
    # Processing: Compose request-shaped phrasings from the entry's description
    # Changed by: Claude Code
    # Date: 2026-10-12
    def generate(self, entry: Entry, *, count: int = 3) -> list[SyntheticQuery]:
        action, is_verb = action_phrase(entry)
        if not action:
            return []

        templates = _VERB_TEMPLATES if is_verb else _NOUN_TEMPLATES
        seed = int(hashlib.blake2b(entry.id.encode(), digest_size=4).hexdigest(), 16)
        queries: list[SyntheticQuery] = []
        seen: set[str] = set()
        for offset in range(min(count, len(templates))):
            template = templates[(seed + offset) % len(templates)]
            text = template.format(action=action, language=entry.language)
            if text in seen:
                continue
            seen.add(text)
            queries.append(
                SyntheticQuery(
                    query=text, entry_id=entry.id, language=entry.language,
                    generator=self.name,
                )
            )
        return queries


class DeepSeekQueryGenerator:
    """Generates queries with a DeepSeek-compatible client.

    The provider sits behind the interface, so swapping it is configuration.
    Raises without a client rather than falling back, so a training set is
    never a silent mixture of model-written and templated queries with nothing
    recording which.
    """

    name = "deepseek"

    def __init__(self, client=None, *, model: str = "deepseek-chat") -> None:
        self.client = client
        self.model = model

    # Input: entry (Entry), count (int)
    # Output: str
    # Processing: Render the generation prompt for one entry
    # Changed by: Claude Code
    # Date: 2026-10-12
    def build_prompt(self, entry: Entry, count: int) -> str:
        """Ask for how a developer would phrase the need, not a restatement.

        The instruction to avoid the function's own name matters: a query
        containing the identifier is answerable by keyword search alone, so it
        teaches the cross-encoder nothing it was built for.
        """
        return (
            f"A developer needs this {entry.language} helper but does not know it exists.\n"
            f"Write {count} short search queries they might type, one per line.\n"
            f"Describe the need in their words. Do NOT use the function's name.\n\n"
            f"Name: {entry.name}\n"
            f"Signature: {entry.signature or 'n/a'}\n"
            f"Description: {entry.description}\n"
        )

    # Input: entry (Entry), count (int)
    # Output: list[SyntheticQuery]
    # Processing: Ask the model for queries a developer would type
    # Changed by: Claude Code
    # Date: 2026-10-12
    def generate(self, entry: Entry, *, count: int = 3) -> list[SyntheticQuery]:
        if self.client is None:
            raise RuntimeError(
                "DeepSeekQueryGenerator has no client; pass one or use "
                "TemplatedQueryGenerator, which needs no credential"
            )
        reply = str(self.client.complete(self.build_prompt(entry, count)))
        queries: list[SyntheticQuery] = []
        for line in reply.splitlines():
            text = line.strip().lstrip("-*0123456789. ").strip()
            if not text:
                continue
            queries.append(
                SyntheticQuery(
                    query=text, entry_id=entry.id, language=entry.language,
                    generator=self.name,
                )
            )
            if len(queries) >= count:
                break
        return queries


# Input: entries (Sequence[Entry]), generator (QueryGenerator | None), per_entry (int)
# Output: list[SyntheticQuery]
# Processing: Generate training queries across a set of entries
# Changed by: Claude Code
# Date: 2026-10-12
def generate_training_queries(
    entries: Sequence[Entry],
    generator: QueryGenerator | None = None,
    *,
    per_entry: int = 3,
) -> list[SyntheticQuery]:
    """Generate queries for every entry. Training only.

    Every result carries the synthetic provenance marker, so none of them can
    be turned into a gold query downstream.
    """
    generator = generator or TemplatedQueryGenerator()
    out: list[SyntheticQuery] = []
    for entry in entries:
        out.extend(generator.generate(entry, count=per_entry))
    return out
