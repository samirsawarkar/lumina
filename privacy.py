"""Privacy check-up: which photos are sitting on identity / secret documents. Counts and kinds only, never values."""

from __future__ import annotations

import re
import time

from . import photos, redact

MAX_CANDIDATES, BUDGET = 150, 20.0  # stage-2 recognitions, seconds
# Stage 1: Apple-index tokens that hint at an identity or secret document (a superset; stage 2 confirms).
_WORDS = {"passport", "licence", "license", "driving", "driver", "identity", "visa", "permit", "residence", "national", "insurance",
          "birth", "password", "passcode", "pin", "iban", "account", "cvv", "debit", "credit", "mastercard", "amex", "social",
          "security", "ssn", "wifi"}
_ID_RE = re.compile(r"licen[cs]e|identity|id card|permit|visa", re.I)
_CARD_RE = re.compile(r"(?<!\d)\d(?:[ \-]?\d){12,18}(?!\d)")
LABELS = {"passport": "Passports", "card": "Payment cards", "password": "Passwords", "id": "ID documents"}


def _luhn(digits: str) -> bool:
    s = sum(d if i % 2 == 0 else (d * 2 - 9 if d > 4 else d * 2) for i, d in enumerate(map(int, reversed(digits))))
    return s % 10 == 0


def _hint(p, toks) -> bool:
    t = set(toks)
    return bool(t & _WORDS or {"wi", "fi"} <= t or getattr(getattr(p, "search_info", None), "id_document_types", None))


def kind_of(lines: list[str]) -> str | None:
    """passport | card | password | id, or None. Precision first: each needs a strong signal."""
    hits = redact.classify(lines)
    kinds = {k for _, k, _, _ in hits}
    joined = " ".join(lines)
    if any(k == "mrz" and "<<" in lines[i] for i, k, _, _ in hits) or (re.search(r"\bpassport\b", joined, re.I) and "number" in kinds):
        return "passport"
    if any(_luhn(re.sub(r"\D", "", m.group())) for l in lines for m in _CARD_RE.finditer(l) if 13 <= len(re.sub(r"\D", "", m.group())) <= 19):
        return "card"
    if "password" in kinds:
        return "password"
    if any(_ID_RE.search(m.group(0)) for m in redact._DOC_TYPE_RE.finditer(joined)) and kinds & {"number", "dob"}:
        return "id"
    return None


def privacy_check(db) -> dict:
    t0 = time.monotonic()
    ps = sorted((p for p in db.photos(movies=True) if not (p.hidden or p.intrash or p.ismovie)), key=photos._ts, reverse=True)
    indexed = photos._has_index(ps)
    # ponytail: without Apple's index this reads only the newest photos until the budget runs out (partial)
    cands = [p for p in ps if not indexed or _hint(p, photos.index_words(p)) or (not photos.index_words(p) and photos._recent(p))]
    partial = len(cands) > MAX_CANDIDATES
    items, seen = [], 0
    for p in cands[:MAX_CANDIDATES]:
        if time.monotonic() - t0 > BUDGET:
            partial = True
            break
        seen += 1
        try:
            lines = photos.ocr_text(p)
            k = kind_of(lines)
            if k:
                items.append({"uuid": p.uuid, "kind": k, "document": redact.safe_name(lines), "date": p.date.isoformat() if p.date else None})
        except Exception:
            continue
    groups = [{"kind": k, "label": LABELS[k], "count": n} for k in LABELS if (n := sum(i["kind"] == k for i in items))]
    return {"scanned": len(ps) if indexed else seen, "count": len(items), "groups": groups, "items": items,
            "all_uuids": [i["uuid"] for i in items[:200]], "partial": partial,
            **({"note": "Only the newest photos were checked this time; say so, and ask again to check more."} if partial else {})}
