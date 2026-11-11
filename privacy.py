"""Privacy check-up: which photos are sitting on identity / secret documents. Counts and kinds only, never values."""

from __future__ import annotations

import re
import time

from . import photos, redact

MAX_CANDIDATES, BUDGET = 150, 20.0  # stage-2 recognitions, seconds
# Stage 1: Apple-index tokens that hint at an identity or secret document (a superset; stage 2 confirms).
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


def _id_types(p) -> bool:
    try:
        return bool(p.search_info.id_document_types)
    except Exception:  # None search_info, or a property that raises: not a candidate this way
        return False


_PP_TOK = {"surname", "given", "nationality", "birth", "issue", "expiry", "authority", "sex"}
_ID_TOK = {"surname", "name", "names", "nationality", "sex", "address", "issued", "issue", "expires", "expiry", "class"}
_CARD_TOK = {"visa", "mastercard", "amex", "debit", "credit", "cvv", "cvc", "cardholder", "card"}


def _hint(p, toks) -> bool:
    """Stage 1, per kind at token level (a superset of what kind_of needs). `toks` are Apple's lowercase word tokens."""
    t = set(toks)
    nums = {x for x in t if x.isdigit() and 4 <= len(x) <= 16}
    # ponytail: an MRZ-only passport (no word 'passport' in the text) is missed here
    return bool(
        "passport" in t and len(t & _PP_TOK) >= 2
        or (t & {"licence", "license", "identity", "permit", "visa"} or {"id", "card"} <= t)
        and (t & {"birth", "dob", "born"} or len(t & _ID_TOK) >= 2)
        or (t & _CARD_TOK or {"valid", "thru"} <= t or {"good", "thru"} <= t or {"american", "express"} <= t)
        and (len(nums) >= 2 or any(len(x) in (15, 16) for x in nums))
        or t & {"password", "passcode", "pin"} and (t & {"wifi", "network", "ssid"} or {"wi", "fi"} <= t or any(c.isdigit() for x in t for c in x))
        or _id_types(p))


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
    quads = " ".join(l.strip() for l in lines if re.fullmatch(r"\d{4}", l.strip()))  # number read as groups of four
    if any(_card(m.group()) for l in [*lines, quads] for m in _CARD_RE.finditer(l)) and _CARD_WORD.search(joined):
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
    order = [(p, False) for p in ps]
    if indexed:  # document-hinted photos first (newest first), then recent ones Apple has not indexed yet, with a small allowance
        order = [(p, False) for p in ps if _hint(p, photos.index_words(p))] + [(p, True) for p in ps if not photos.index_words(p) and photos._recent(p)]
    cache, items, new, checked, rt, late_skipped, hint_skipped = photos._load_cache(), [], 0, 0, None, False, False
    for p, late in order:  # cached photos are free, only new recognitions count against the caps
        lines = photos._cached_lines(cache, p)
        cached = lines is not None
        if not cached:
            now = time.monotonic()
            rt = rt or now if late else rt
            if new >= MAX_CANDIDATES or now - t0 > BUDGET or late and now - rt > photos.RECENT_BUDGET:
                late_skipped, hint_skipped = late_skipped or late, hint_skipped or not late
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
    cands = order
    partial = checked < len(cands)
    notes = ([photos.RECENT_NOTE if late_skipped and not hint_skipped else
              f"Checked {checked} of {len(cands)} likely documents, newest first; say so, and asking again continues."] if partial else []) + (
        ["Lumina checked the text Apple Photos has already recognised, so very new photos or a library Apple is still processing may not be fully covered."]
        if indexed else [])
    groups = [{"kind": k, "label": LABELS[k], "count": n} for k in LABELS if (n := sum(i["kind"] == k for i in items))]
    return {"scanned": len(ps) if indexed else checked, "count": len(items), "groups": groups, "items": items,
            "all_uuids": [i["uuid"] for i in items[:200]], "partial": partial,
            "checked": checked, "candidates": len(cands), "source": "apple_index" if indexed else "live",
            **({"note": " ".join(notes)} if notes else {})}
