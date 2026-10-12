"""Descriptions for entries that arrived without one.

An entry with no description is unreachable. The embedder has nothing to
encode and a query has nothing to match, so the entry sits in the library and
is never recommended however good the code is. 300 of 515 harvested entries
are in that state.

The hard constraint here is honesty, not quality. A generator must not claim
behaviour the code does not show. A plausible sentence invented from a
function name is *worse* than a thin one: it gets indexed, matched, and
surfaced on a card as though someone had verified it, and nobody reading that
card can tell the difference. So the default generator restates the identifier
and the signature and stops, and every description records where it came from.
"""

from __future__ import annotations

import re
from enum import Enum
from typing import Protocol, runtime_checkable

_CAMEL_BOUNDARY = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")
_SIGNATURE_PARAMS = re.compile(r"^\((?P<params>.*?)\)(?:\s*->\s*(?P<returns>.+))?$", re.S)



class DescriptionSource(str, Enum):
    """Where an entry's description came from.

    Recorded because a derived description is otherwise indistinguishable from
    an author's own. Three things depend on telling them apart: the reusability
    model's ``documentation`` signal should not weigh them alike, a reviewer
    needs to know which entries still want a real description, and measuring
    retrieval quality per source needs them grouped.
    """

    DOCSTRING = "docstring"
    NAME_AND_SIGNATURE = "name_and_signature"
    GENERATED = "generated"

    @property
    def is_authored(self) -> bool:
        """Whether a person wrote it."""
        return self is DescriptionSource.DOCSTRING


@runtime_checkable
class DescriptionGenerator(Protocol):
    """Produces a description for an entry. The provider is configuration."""

    source: DescriptionSource

    def describe(self, *, name: str, signature: str | None, language: str, code: str) -> str: ...


# Input: identifier (str)
# Output: str
# Processing: Split snake_case and camelCase into a readable word sequence
# Changed by: Claude Code
# Date: 2026-10-12
def words_from_identifier(identifier: str) -> str:
    """``stratified_split`` to "stratified split", ``readFileSync`` to "read file sync"."""
    pieces: list[str] = []
    for chunk in identifier.strip("_").split("_"):
        if not chunk:
            continue
        pieces.extend(part for part in _CAMEL_BOUNDARY.split(chunk) if part)
    return " ".join(p.lower() for p in pieces)


# Input: signature (str | None)
# Output: tuple[list[str], str | None]
# Processing: Split a signature into parameter names and a return annotation
# Changed by: Claude Code
# Date: 2026-10-12
def parse_signature(signature: str | None) -> tuple[list[str], str | None]:
    """Pull parameter names and the return type out of a signature string.

    Defaults, annotations and ``self`` are dropped: the description needs the
    shape of the call, not a restatement of the whole signature, which the card
    already carries in its own field.
    """
    if not signature:
        return [], None
    match = _SIGNATURE_PARAMS.match(signature.strip())
    if match is None:
        return [], None

    params: list[str] = []
    depth = 0
    current = ""
    for char in match.group("params") or "":
        if char in "[({":
            depth += 1
        elif char in "])}":
            depth -= 1
        if char == "," and depth == 0:
            params.append(current)
            current = ""
        else:
            current += char
    if current.strip():
        params.append(current)

    cleaned: list[str] = []
    for raw in params:
        name = raw.split("=")[0].split(":")[0].strip()
        # C and C++ put the type before the name, space-separated, so the
        # parameter is the trailing identifier: "float *x" is x, not "float *x".
        if " " in name:
            name = name.rsplit(" ", 1)[-1]
        name = name.lstrip("*&").strip()
        if name and name not in {"self", "cls"}:
            cleaned.append(name)

    returns = (match.group("returns") or "").strip() or None
    if returns in {"None", "void"}:
        returns = None
    return cleaned, returns


class NameSignatureDescriber:
    """Restates the identifier and signature. Claims nothing beyond them.

    The default, because it needs no API key and so cannot block a harvest on
    a credential. Deliberately thin: everything it says is visible in the
    declaration it was read from, which is the only way a generated description
    can be trusted without a person checking it.
    """

    source = DescriptionSource.NAME_AND_SIGNATURE

    # Input: name (str), signature (str | None), language (str), code (str)
    # Output: str
    # Processing: Build a description from the identifier and signature only
    # Changed by: Claude Code
    # Date: 2026-10-12
    def describe(self, *, name: str, signature: str | None, language: str, code: str) -> str:
        phrase = words_from_identifier(name)
        if not phrase:
            return ""

        params, returns = parse_signature(signature)

        # The identifier words, capitalised, and nothing added. An earlier
        # version branched on whether the name led with a known verb and then
        # produced the same string either way -- the branch implied a
        # distinction the output never made, so it is gone.
        sentence = phrase[0].upper() + phrase[1:] + "."

        clauses: list[str] = []
        if params:
            clauses.append(f"Takes {', '.join(params)}")
        if returns:
            clauses.append(f"returns {returns}")
        if clauses:
            sentence += " " + "; ".join(clauses) + "."
        return sentence


class LLMDescriber:
    """Describes an entry with a language model.

    Raises rather than degrading when no client is configured. A silent
    fallback would mean some entries carry a model-written description and
    others a restated identifier, with nothing in the record saying which --
    exactly the ambiguity ``description_source`` exists to prevent.
    """

    source = DescriptionSource.GENERATED

    def __init__(self, client=None, *, max_words: int = 20) -> None:
        self.client = client
        self.max_words = max_words

    # Input: name (str), signature (str | None), language (str), code (str)
    # Output: str
    # Processing: Ask the configured model for a one-line description
    # Changed by: Claude Code
    # Date: 2026-10-12
    def describe(self, *, name: str, signature: str | None, language: str, code: str) -> str:
        if self.client is None:
            raise RuntimeError(
                "LLMDescriber has no client; pass one or use NameSignatureDescriber, "
                "which needs no credential"
            )
        prompt = (
            f"One sentence, at most {self.max_words} words, describing what this "
            f"{language} function does. State only what the code shows. No preamble.\n\n"
            f"{code[:4000]}"
        )
        return str(self.client.complete(prompt)).strip()


# Input: record (dict), generator (DescriptionGenerator)
# Output: dict
# Processing: Fill a record's missing description and record its source
# Changed by: Claude Code
# Date: 2026-10-12
def describe_record(record: dict, generator: DescriptionGenerator | None = None) -> dict:
    """Fill in a dataset record's description, in place, and say where from.

    A record that already has an authored description is left untouched: an
    author's own words beat anything derived, and overwriting them would lose
    the only description in the pipeline nobody has to second-guess.

    An entry described from its name keeps ``description`` in ``missing``,
    because a restated identifier is a placeholder, not a description.
    """
    generator = generator or NameSignatureDescriber()

    if record.get("description"):
        record.setdefault("description_source", DescriptionSource.DOCSTRING.value)
        return record

    text = generator.describe(
        name=record.get("name", ""),
        signature=record.get("signature"),
        language=record.get("language", ""),
        code=record.get("code", ""),
    )
    if not text:
        record["description_source"] = None
        return record

    record["description"] = text
    record["description_source"] = generator.source.value
    missing = list(record.get("missing", []))
    if generator.source is DescriptionSource.NAME_AND_SIGNATURE:
        if "description" not in missing:
            missing.append("description")
    else:
        missing = [m for m in missing if m != "description"]
    record["missing"] = missing
    return record
