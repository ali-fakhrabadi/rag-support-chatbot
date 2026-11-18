"""Citation validation and abstention decisions for model output."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

ABSTAIN_MESSAGE = (
    "I couldn't find this in the documents you have access to, so I can't answer it reliably."
)
_MARKER = re.compile(r"\[\s*(S\d+(?:\s*,\s*S\d+)*)\s*\]")


@dataclass(frozen=True)
class ValidatedAnswer:
    text: str
    cited_ids: list[str]  # valid ids, in order of first use
    abstained: bool
    reason: str | None  # why it abstained, or why citations were dropped
    dropped_ids: list[str]


def _ids_in_text(text: str) -> list[str]:
    ids: list[str] = []
    for group in _MARKER.findall(text):
        for raw in group.split(","):
            sid = raw.strip()
            if sid not in ids:
                ids.append(sid)
    return ids


def validate_answer(data: dict[str, Any], allowed_ids: set[str]) -> ValidatedAnswer:
    """Keep only citations to authorised sources; abstain when the answer is not grounded."""
    answer = data.get("answer")
    raw_citations = data.get("citations")
    insufficient = data.get("insufficient_evidence")
    if not isinstance(answer, str) or not isinstance(insufficient, bool):
        return ValidatedAnswer(ABSTAIN_MESSAGE, [], True, "malformed_output", [])
    if not isinstance(raw_citations, list):
        raw_citations = []

    answer = answer.strip()
    if insufficient or not answer:
        return ValidatedAnswer(ABSTAIN_MESSAGE, [], True, "model_insufficient_evidence", [])

    claimed = [str(c).strip() for c in raw_citations if isinstance(c, str | int)]
    in_text = _ids_in_text(answer)
    dropped = [sid for sid in dict.fromkeys(claimed + in_text) if sid not in allowed_ids]

    def _rewrite(match: re.Match[str]) -> str:
        kept = [s.strip() for s in match.group(1).split(",") if s.strip() in allowed_ids]
        return "".join(f"[{s}]" for s in kept)

    cleaned = re.sub(r"\s+([.,;:!?])", r"\1", _MARKER.sub(_rewrite, answer)).strip()
    cited: list[str] = list(_ids_in_text(cleaned))
    # Ids listed in "citations" but not marked in the text still count if they are authorised.
    cited += [sid for sid in claimed if sid in allowed_ids and sid not in cited]

    if not cited:
        return ValidatedAnswer(ABSTAIN_MESSAGE, [], True, "no_valid_citation", dropped)
    return ValidatedAnswer(cleaned, cited, False, "dropped_citations" if dropped else None, dropped)
