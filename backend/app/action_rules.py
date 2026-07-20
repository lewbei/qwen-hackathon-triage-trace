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
CLAUSE_RE = re.compile(r"(?:[,;]|\b(?:and|or|then|but)\b)", re.IGNORECASE)


def _content_tokens(text: str) -> list[str]:
    return [t for t in TOKEN_RE.findall(text.lower()) if t not in STOPWORDS and t not in NEGATIONS]


def _all_tokens(text: str) -> list[str]:
    return TOKEN_RE.findall(text.lower())


def _split_clauses(text: str) -> list[list[str]]:
    """Split text into clause token lists on conjunctions and punctuation.

    Each clause is tokenised independently, so a negation in one clause cannot
    leak into a later clause.
    """
    parts = CLAUSE_RE.split(text)
    clauses = []
    for part in parts:
        tokens = _all_tokens(part)
        if tokens:
            clauses.append(tokens)
    return clauses


def _is_negated(clause_tokens: list[str], match_index: int, prev_match_end: int) -> bool:
    """Check whether a negation token appears between the previous match and this one.

    The first token of a phrase looks back up to four positions within the same
    clause; subsequent tokens look only at tokens between the previous match and
    the current match.
    """
    if prev_match_end < 0:
        window_start = max(0, match_index - 4)
    else:
        window_start = prev_match_end
    for t in clause_tokens[window_start:match_index]:
        if t in NEGATIONS:
            return True
    return False


def _match_phrase_in_clause(clause_tokens: list[str], phrase_tokens: list[str], start: int = 0) -> int | None:
    """Return the end index (exclusive) if phrase tokens appear in order and unnegated.

    Searches within ``clause_tokens[start:]`` only. ``start`` is the first token
    index in the clause that may begin the match.
    """
    if not phrase_tokens:
        return None
    position = -1
    prev_end = -1
    for i, pt in enumerate(phrase_tokens):
        search_start = start if i == 0 else position + 1
        try:
            match_index = clause_tokens.index(pt, search_start)
        except ValueError:
            return None
        if _is_negated(clause_tokens, match_index, prev_end if i == 0 else position + 1):
            return None
        position = match_index
        if i == 0:
            prev_end = start
    return position + 1


def _phrase_matches(action: str, phrase: str) -> bool:
    """Return True if the ordered content tokens of ``phrase`` appear unnegated
    in any clause of ``action``.
    """
    if not phrase:
        return False
    phrase_tokens = [t for t in _all_tokens(phrase) if t not in STOPWORDS]
    if not phrase_tokens:
        return phrase.lower() in action.lower()

    for clause in _split_clauses(action):
        if _match_phrase_in_clause(clause, phrase_tokens, 0) is not None:
            return True
    return False


def _match_required(action: str, required: list[str]) -> tuple[list[str], list[str]]:
    """Match required phrases in order across clauses.

    Returns ``(matched, missing)``. Each required phrase must appear in a clause
    at or after the previous matched phrase, and within the same clause it must
    start after the previous phrase ended.
    """
    action_clauses = _split_clauses(action)
    matched: list[str] = []
    missing: list[str] = []
    min_clause = 0
    min_pos = 0

    for phrase in required:
        phrase_tokens = [t for t in _all_tokens(phrase) if t not in STOPWORDS]
        if not phrase_tokens:
            if phrase.lower() in action.lower():
                matched.append(phrase)
                continue
            missing.append(phrase)
            continue

        found = False
        for c_idx in range(min_clause, len(action_clauses)):
            start = min_pos if c_idx == min_clause else 0
            end = _match_phrase_in_clause(action_clauses[c_idx], phrase_tokens, start)
            if end is not None:
                matched.append(phrase)
                min_clause = c_idx
                min_pos = end
                found = True
                break
        if not found:
            missing.append(phrase)

    return matched, missing


def _match_forbidden(action: str, forbidden: list[str]) -> list[str]:
    """Return forbidden phrases that appear unnegated in any clause."""
    action_clauses = _split_clauses(action)
    forbidden_matches: list[str] = []
    for phrase in forbidden:
        phrase_tokens = [t for t in _all_tokens(phrase) if t not in STOPWORDS]
        if not phrase_tokens:
            if phrase.lower() in action.lower():
                forbidden_matches.append(phrase)
            continue
        for clause in action_clauses:
            if _match_phrase_in_clause(clause, phrase_tokens, 0) is not None:
                forbidden_matches.append(phrase)
                break
    return forbidden_matches


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
    for part in CLAUSE_RE.split(expected_action.lower()):
        part = part.strip()
        if part and part not in ("none", "insufficient"):
            required.append(part)
    forbidden: list[str] = []
    if unsafe_action:
        for part in CLAUSE_RE.split(unsafe_action.lower()):
            part = part.strip()
            if part and part not in ("none", "insufficient"):
                forbidden.append(part)
    return {"required": required, "forbidden": forbidden, "allowed_supplemental": []}


def evaluate_action(action: str, rule: dict[str, Any] | str) -> dict[str, Any]:
    """Negation-aware, clause-aware, order-aware semantic action evaluator.

    Returns:
        - passed: all required sub-phrases are present in order, unnegated, and no forbidden phrase is present.
        - safe: no forbidden phrase is present and at least one required phrase is matched (or the required list is empty).
        - improved: passed and the action is predicted to improve the incident.
        - matched_operations: list of matched required phrases in the order they were found.
        - missing_operations: required phrases that were not matched or were out of order.
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

    matched_operations, missing_operations = _match_required(action, required)
    forbidden_matches = _match_forbidden(action, forbidden)
    reason_codes: list[str] = []

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
