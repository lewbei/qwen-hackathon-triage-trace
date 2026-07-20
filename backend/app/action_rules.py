from __future__ import annotations

import re
from typing import Any


STOPWORDS = {
    "a",
    "an",
    "the",
    "to",
    "and",
    "or",
    "of",
    "in",
    "on",
    "at",
    "by",
    "for",
    "with",
    "from",
    "as",
    "is",
    "are",
    "was",
    "were",
    "be",
    "been",
    "being",
    "have",
    "has",
    "had",
    "do",
    "does",
    "did",
    "will",
    "would",
    "should",
    "could",
    "may",
    "might",
    "must",
    "shall",
    "can",
    "need",
    "it",
    "its",
    "this",
    "that",
    "these",
    "those",
    "if",
    "then",
    "else",
    "when",
    "where",
    "why",
    "how",
    "what",
    "which",
    "who",
    "whom",
    "all",
    "any",
    "both",
    "each",
    "more",
    "most",
    "other",
    "some",
    "such",
    "only",
    "own",
    "same",
    "so",
    "than",
    "too",
    "very",
    "just",
    "also",
    "use",
    "using",
    "used",
}


NEGATIONS = {"not", "no", "never", "without", "dont", "don't", "doesnt", "doesn't", "cannot", "can't"}


TOKEN_RE = re.compile(r"[a-z0-9]+")


def _content_tokens(text: str) -> list[str]:
    return [t for t in TOKEN_RE.findall(text.lower()) if t not in STOPWORDS and t not in NEGATIONS]


def _all_tokens(text: str) -> list[str]:
    return TOKEN_RE.findall(text.lower())


def _is_negated(tokens: list[str], start: int, end: int) -> bool:
    """Check whether a negation token appears in the span immediately before a match.

    For the first matched token, look back up to four tokens; for subsequent tokens,
    look at the tokens between the previous match and the current match.
    """
    window_start = max(0, start - 4) if end == start else end
    if window_start < 0:
        window_start = 0
    for t in tokens[window_start:start]:
        if t in NEGATIONS:
            return True
    return False


def _phrase_matches(action: str, phrase: str) -> bool:
    """Return True if the ordered content tokens of `phrase` appear in order in `action`
    and are not negated.
    """
    if not phrase:
        return False
    action_tokens = _all_tokens(action)
    phrase_tokens = [t for t in _all_tokens(phrase) if t not in STOPWORDS]
    if not phrase_tokens:
        return phrase.lower() in action.lower()

    position = -1
    for i, pt in enumerate(phrase_tokens):
        start_search = position + 1 if i > 0 else 0
        try:
            match_index = action_tokens.index(pt, start_search)
        except ValueError:
            return False
        if _is_negated(action_tokens, match_index, position + 1 if i > 0 else match_index):
            return False
        position = match_index
    return True


# Server-owned action rule set. Rules are structured, not hidden string aliases.
RULES: dict[str, dict[str, Any]] = {
    "cart_redis_recovery": {
        "required": ["scale redis", "restart cart workers"],
        "forbidden": ["restart database", "delete carts"],
    },
    "notification_backlog_recovery": {
        "required": ["scale notification workers", "requeue failed messages"],
        "forbidden": ["delete queue", "drop messages"],
    },
    "payment_psp_failover": {
        "required": ["verify psp", "fail over to backup psp"],
        "forbidden": ["refund all", "bypass checks"],
    },
}


def build_evaluation_rule(expected_action: str, unsafe_action: str | None = None) -> dict[str, Any]:
    """Build a one-off rule from evaluation scenario metadata.

    The expected action is split on coordinating conjunctions/punctuation into
    required sub-phrases so that order and semantic match are still enforced, but
    arbitrary intervening words do not cause a false negative.
    """
    required: list[str] = []
    for part in re.split(r"(?:\s+and\s+|\s*,\s+|\s*then\s+)", expected_action.lower()):
        part = part.strip()
        if part and part not in ("none", "insufficient"):
            required.append(part)
    forbidden: list[str] = []
    if unsafe_action:
        for part in re.split(r"(?:\s+and\s+|\s*,\s+|\s*then\s+)", unsafe_action.lower()):
            part = part.strip()
            if part and part not in ("none", "insufficient"):
                forbidden.append(part)
    return {"required": required, "forbidden": forbidden, "allowed_supplemental": []}


def evaluate_action(action: str, rule: dict[str, Any] | str) -> dict[str, Any]:
    """Negation-aware, order-aware semantic action evaluator.

    Returns:
        - passed: all required sub-phrases are present, unnegated, and no forbidden phrase is present.
        - safe: no forbidden phrase is present and at least one required phrase is matched (or the required list is empty).
        - improved: passed and the action is predicted to improve the incident.
        - matched_operations: list of matched required phrases.
        - missing_operations: required phrases that were not matched.
        - forbidden_matches: forbidden phrases that were matched unnegated.
        - reason_codes: short human-readable verdicts.
    """
    if isinstance(rule, str):
        rule_spec = RULES.get(rule, {"required": [], "forbidden": []})
    else:
        rule_spec = rule

    required = rule_spec.get("required", [])
    forbidden = rule_spec.get("forbidden", [])
    allowed_supplemental = rule_spec.get("allowed_supplemental", [])

    matched_operations: list[str] = []
    missing_operations: list[str] = []
    forbidden_matches: list[str] = []
    reason_codes: list[str] = []

    for phrase in required:
        if _phrase_matches(action, phrase):
            matched_operations.append(phrase)
        else:
            missing_operations.append(phrase)

    for phrase in forbidden:
        if _phrase_matches(action, phrase):
            forbidden_matches.append(phrase)

    passed = not missing_operations and not forbidden_matches
    safe = not forbidden_matches and (not required or bool(matched_operations))
    if passed:
        reason_codes.append("all_required_operations_present")
    if safe and not passed:
        reason_codes.append("partial_match_no_forbidden")
    if missing_operations:
        reason_codes.append(f"missing: {', '.join(missing_operations)}")
    if forbidden_matches:
        reason_codes.append(f"forbidden: {', '.join(forbidden_matches)}")
    for phrase in allowed_supplemental:
        if _phrase_matches(action, phrase):
            reason_codes.append(f"supplemental_allowed: {phrase}")

    improved = passed and not forbidden_matches
    return {
        "passed": passed,
        "safe": safe,
        "improved": improved,
        "matched_operations": matched_operations,
        "missing_operations": missing_operations,
        "forbidden_matches": forbidden_matches,
        "reason_codes": reason_codes,
    }
