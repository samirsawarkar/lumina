"""Privacy check-up: which photos are sitting on identity / secret documents. Counts and kinds only, never values."""

from __future__ import annotations

import re
import time

from . import photos, redact

MAX_CANDIDATES, BUDGET = 150, 20.0  # stage-2 recognitions, seconds
# Stage 1: Apple-index tokens that hint at an identity or secret document (a superset; stage 2 confirms).
_WORDS = {"passport", "licence", "license", "identity", "permit", "visa", "password", "passcode", "pin", "wifi",
          "mastercard", "amex", "debit", "credit", "cvv", "cvc"}  # plus id+card, wi+fi, valid+thru
_ID_RE = re.compile(r"licen[cs]e|identity|id card|permit|visa", re.I)
_CARD_RE = re.compile(r"(?<!\d)\d(?:[ \-]?\d){12,18}(?!\d)")
_CARD_WORD = re.compile(r"\b(visa|mastercard|amex|american express|debit|credit|valid\s+thru|good\s+thru|cvv|cvc|cardholder)\b|(?<!\d)(0[1-9]|1[0-2])\s*/\s*\d{2}(\d{2})?(?!\d)", re.I)
_CARD_SHAPE = re.compile(r"4\d{15}|5[1-5]\d{14}|2[2-7]\d{14}|6\d{15}|3[47]\d{13}")
_PP_LABELS = ("surname", r"given names?", "nationality", "date of birth", "place of birth", "date of issue", r"(?:date of )?expiry", "authority", "sex")
_ID_LABELS = ("surname", r"names?", "date of birth", "nationality", "sex", "address", r"issued|date of issue", r"expires|expiry", "class")
_NET = re.compile(r"\b(wi-?fi|network|ssid)\b", re.I)
LABELS = {"passport": "Passports", "card": "Payment cards", "password": "Passwords", "id": "ID documents"}


def _luhn(digits: str) -> bool:
    s = sum(d if i % 2 == 0 else (d * 2 - 9 if d > 4 else d * 2) for i, d in enumerate(map(int, reversed(digits))))
    return s % 10 == 0


def _hint(p, toks) -> bool:
    t = set(toks)
    return bool(t & _WORDS or {"wi", "fi"} <= t or {"id", "card"} <= t or {"valid", "thru"} <= t
                or getattr(getattr(p, "search_info", None), "id_document_types", None))


def _labels(joined, names) -> int:
    return sum(bool(re.search(rf"\b(?:{n})\b", joined, re.I)) for n in names)


def _card(l: str) -> bool:
    d = re.sub(r"\D", "", l)
    return bool(_CARD_SHAPE.fullmatch(d)) and _luhn(d)


def kind_of(lines: list[str]) -> str | None:
    """passport | card | password | id, or None. Precision first: each needs strong signals (no bare number rules)."""
    hits = redact.classify(lines)
    joined = " ".join(lines)
    if any(k == "mrz" and "<<" in lines[i] for i, k, _, _ in hits) or (re.search(r"\bpassport\b", joined, re.I) and _labels(joined, _PP_LABELS) >= 2):
        return "passport"
    if any(_card(m.group()) for l in lines for m in _CARD_RE.finditer(l)) and _CARD_WORD.search(joined):
        return "card"
    if any(k == "password" and (re.search(r"\d", lines[i][a:b]) or _NET.search(joined)) for i, k, a, b in hits):
        return "password"
    if any(_ID_RE.search(m.group(0)) for m in redact._DOC_TYPE_RE.finditer(joined)) and (
            any(k == "dob" for _, k, _, _ in hits) or _labels(joined, _ID_LABELS) >= 2):
        return "id"
    return None


def _document(kind, lines) -> str:
    """Fixed vocabulary only: a document-type word from redact's list, else a per-kind name. Never recognised free text."""
    if kind in ("passport", "id"):
        for m in redact._DOC_TYPE_RE.finditer(" ".join(lines)):
            if kind == "passport" and m.group(0).lower() == "passport" or kind == "id" and _ID_RE.search(m.group(0)):
                return m.group(0).title()
    return {"passport": "Passport", "card": "Payment card", "password": "Password", "id": "ID document"}[kind]


def privacy_check(db) -> dict:
    t0 = time.monotonic()
    ps = sorted((p for p in db.photos(movies=True) if not (p.hidden or p.intrash or p.ismovie)), key=photos._ts, reverse=True)
    indexed = photos._has_index(db, ps)
    # ponytail: without Apple's index this reads only the newest photos until the budget runs out (partial)
    cands = [p for p in ps for t in [photos.index_words(p) if indexed else ()] if not indexed or _hint(p, t) or (not t and photos._recent(p))]
    cache, items, new, checked = photos._load_cache(), [], 0, 0
    for p in cands:  # newest first; cached photos are free, only new recognitions count against the caps
        lines = photos._cached_lines(cache, p)
        cached = lines is not None
        if not cached and (new >= MAX_CANDIDATES or time.monotonic() - t0 > BUDGET):
            continue
        checked += 1
        new += not cached
        try:
            lines = lines if cached else photos.ocr_text(p)
            k = kind_of(lines)
            if k:
                items.append({"uuid": p.uuid, "kind": k, "document": _document(k, lines), "date": p.date.isoformat() if p.date else None})
        except Exception:
            continue
    partial = checked < len(cands)
    notes = ([f"Checked {checked} of {len(cands)} likely documents, newest first; say so, and asking again continues."] if partial else []) + (
        ["Lumina checked the text Apple Photos has already recognised, so very new photos or a library Apple is still processing may not be fully covered."]
        if indexed else [])
    groups = [{"kind": k, "label": LABELS[k], "count": n} for k in LABELS if (n := sum(i["kind"] == k for i in items))]
    return {"scanned": len(ps) if indexed else checked, "count": len(items), "groups": groups, "items": items,
            "all_uuids": [i["uuid"] for i in items[:200]], "partial": partial,
            "checked": checked, "candidates": len(cands), "source": "apple_index" if indexed else "live",
            **({"note": " ".join(notes)} if notes else {})}
