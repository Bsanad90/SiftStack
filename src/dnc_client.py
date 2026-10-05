"""DNC (Do Not Call) scrub: a per-number cache plus a vendor adapter.

The cache (`output/dnc_cache.json`) holds, per 10-digit number, one entry per
SOURCE that has spoken about it:

    {"3015551234": {"vendor":   {"dnc": false, "federal": false, "state": false,
                                 "litigator": false, "wireless": true,
                                 "reason": "", "checked_at": "2026-10-05T..."},
                    "tracerfy": {"dnc": true, "checked_at": "..."},
                    "datasift": {"dnc": true, "reason": "CORRECT_DNC", ...},
                    "smartercontact": {"dnc": true, "reason": "Do Not Call", ...}}}

Rules (Basem, 2026-10-05):
- A DNC HIT never expires and no source can clear it. Tracerfy's flag, a
  DataSift DNC status and Smarter Contact's blocked-numbers export only ever ADD
  a mark. (Smarter Contact scrubs at send time, 2026-10-05: its export is how
  its DNC / opt-out / litigator blocks reach dial sheets built outside it.)
- A CLEAN answer counts only when it comes from the paid vendor and is no older
  than FRESH_DAYS (31: the FTC registry refresh). Older = stale = unchecked,
  the same idea as a Trestle cache entry with `litigator: None`.
- A vendor error is an ERROR, never "clean". `scrub_remote` raises; it does not
  return an empty result.

The vendor is not chosen yet (see docs/api/dnc_vendors.md). Until it is,
`scrub_remote` raises DncVendorNotConfigured and every caller stays cache-only.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CACHE_PATH = ROOT / "output" / "dnc_cache.json"
FRESH_DAYS = 31
VENDOR = "vendor"
SOURCES = (VENDOR, "tracerfy", "datasift", "smartercontact")
DNC_TAG = "DNC"

_cache: dict | None = None
_cache_path: Path | None = None


class DncVendorNotConfigured(RuntimeError):
    """No DNC vendor adapter / key yet. Nothing was checked and nothing was spent."""


class DncVendorError(RuntimeError):
    """The vendor call failed. The numbers in it are UNCHECKED, not clean."""


def clean_number(raw) -> str:
    digits = "".join(ch for ch in str(raw or "") if ch.isdigit())
    if len(digits) == 11 and digits.startswith("1"):
        digits = digits[1:]
    return digits if len(digits) == 10 else ""


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _parse(ts: str | None) -> datetime | None:
    if not ts:
        return None
    try:
        dt = datetime.fromisoformat(ts)
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


# ───────────────────────── cache ─────────────────────────

def load_cache(path: Path | None = None) -> dict:
    global _cache, _cache_path
    path = path or _cache_path or CACHE_PATH
    if _cache is None or _cache_path != path:
        _cache = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        _cache_path = path
    return _cache


def save_cache(cache: dict | None = None, path: Path | None = None) -> None:
    path = path or _cache_path or CACHE_PATH
    cache = cache if cache is not None else load_cache(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(cache, indent=1, sort_keys=True), encoding="utf-8")
    tmp.replace(path)


def reset_cache_for_tests(cache: dict | None = None, path: Path | None = None) -> None:
    global _cache, _cache_path
    _cache, _cache_path = cache, path


def record(number, source: str, dnc: bool, cache: dict | None = None, **fields) -> None:
    """Store one source's answer. A secondary source can only ADD a hit: a clean
    answer from tracerfy / datasift is not stored as clearance."""
    if source not in SOURCES:
        raise ValueError(f"unknown DNC source {source!r}")
    n = clean_number(number)
    if not n:
        return
    cache = cache if cache is not None else load_cache()
    if source != VENDOR and not dnc:
        return
    entry = {"dnc": bool(dnc), "checked_at": fields.pop("checked_at", None) or _now().isoformat()}
    entry.update(fields)
    slot = cache.setdefault(n, {})
    prior = slot.get(source)
    if prior and prior.get("dnc") and not entry["dnc"]:
        return  # a hit never clears
    slot[source] = entry


def status(number, cache: dict | None = None, now: datetime | None = None) -> str:
    """'dnc' | 'clean' | 'stale' | 'unchecked' for one number."""
    n = clean_number(number)
    cache = cache if cache is not None else load_cache()
    slot = cache.get(n) or {}
    if any((e or {}).get("dnc") for e in slot.values()):
        return "dnc"
    v = slot.get(VENDOR)
    if not v:
        return "unchecked"
    at = _parse(v.get("checked_at"))
    if at is None or (now or _now()) - at > timedelta(days=FRESH_DAYS):
        return "stale"
    return "clean"


def is_dnc(number, cache: dict | None = None) -> bool:
    try:
        return status(number, cache) == "dnc"
    except (OSError, ValueError):
        # An unreadable cache must not crash a scorer, but it is not "clean"
        # either -- dnc_scrub.py reports the cache state separately.
        return False


def dnc_sources(number, cache: dict | None = None) -> list[str]:
    cache = cache if cache is not None else load_cache()
    slot = cache.get(clean_number(number)) or {}
    return [s for s in SOURCES if (slot.get(s) or {}).get("dnc")]


def override_tag(number, tag: str, cache: dict | None = None) -> str:
    """The phone tag after the DNC rule. Litigator Risk keeps its own tag (it is
    the stronger reason); any other tag becomes DNC when the number is a hit."""
    if tag == "Litigator Risk":
        return tag
    return DNC_TAG if is_dnc(number, cache) else tag


# ───────────────────────── vendor ─────────────────────────

def price_per_number() -> float | None:
    raw = os.getenv("DNC_PRICE_PER_NUMBER", "").strip()
    try:
        return float(raw) if raw else None
    except ValueError:
        return None


def vendor_configured() -> bool:
    return bool(os.getenv("DNC_API_KEY")) and _ADAPTER is not None


def scrub_remote(numbers: list[str]) -> dict[str, dict]:
    """Ask the paid vendor about `numbers`. Returns {number: entry} for EVERY
    number asked, or raises. Callers record() the entries and save_cache().

    PAID CALL. Only `dnc_scrub.py --spend` reaches this, and only on Basem's go.
    """
    if not vendor_configured():
        raise DncVendorNotConfigured(
            "No DNC vendor adapter/key yet (docs/api/dnc_vendors.md). Nothing checked, nothing spent.")
    out = _ADAPTER(numbers, os.environ["DNC_API_KEY"])
    missing = [n for n in numbers if n not in out]
    if missing:
        raise DncVendorError(f"vendor answered {len(out)} of {len(numbers)}; "
                             f"{len(missing)} UNCHECKED (first: {missing[0]})")
    return out


# The chosen vendor's adapter goes here: fn(numbers, api_key) -> {number: entry}
# with entry keys dnc / federal / state / litigator / wireless / reason. It must
# RAISE DncVendorError on any HTTP / parse failure, never return a partial dict.
_ADAPTER = None
