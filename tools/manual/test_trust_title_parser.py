"""Offline guard for obituary_dp_batch.parse_owner_blob -- the trust-title -> trustee parser.

Run after ANY change to parse_owner_blob / _order_tokens / TRUST_WORDS:

    python tools/manual/test_trust_title_parser.py

Every case is a title seen live (docstrings, the 2026-08 pilot, the 2026-09-23 lane cohort).
The two 2026-09-23 defects: "Discretionary" read as a surname, and a trailing "Trustee"
forcing recorder order onto a FIRST MIDDLE LAST name.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "src"), str(ROOT / "src" / "scripts")]
from obituary_dp_batch import parse_owner_blob  # noqa: E402

# (title, expected first, expected last, expected source)
CASES = [
    # docstring / pilot examples
    ("Condon Mark Stephen Tr", "Mark", "Condon", "trust_title"),
    ("Norma Veltri Trust", "Norma", "Veltri", "trust_title"),
    ("Adams Belle H & Harold B Trs", "Belle", "Adams", "trust_title"),
    ("Larry And Joanne Stevens Living Trust", "Larry", "Stevens", "trust_title"),
    ("Christine Hopkins Family Revocable Tr", "Christine", "Hopkins", "trust_title"),
    ("Sebastian Michael J Rev Trust", "Michael", "Sebastian", "trust_title"),
    ("Anderson David F", "David", "Anderson", "blob"),
    ("Kevin J Skiffington", "Kevin", "Skiffington", "blob"),
    ("Ford Ronald", "Ronald", "Ford", "blob"),
    ("Jane Takeuchi Udelson", "Jane", "Udelson", "blob"),
    # 2026-09-23 lane cohort
    ("Joseph C Hottel Jr Discretionary Trust", "Joseph", "Hottel", "trust_title"),
    ("Nathan Wayne Gross Trustee", "Nathan", "Gross", "trust_title"),
    ("Gross Nathan Wayne Trustee", "Nathan", "Gross", "trust_title"),
    ("Mary E Mehl Living Trust", "Mary", "Mehl", "trust_title"),
    ("Manteuffel Thomas O Tr", "Thomas", "Manteuffel", "trust_title"),
    ("Brennan Pamela Kathleen Tr", "Pamela", "Brennan", "trust_title"),
    ("Michael George Amouri Trust", "Michael", "Amouri", "trust_title"),
    ("Neil Sandberg Revocable Trust", "Neil", "Sandberg", "trust_title"),
    ("Swassing Charles R 2nd", "Charles", "Swassing", "blob"),
    ("Crabbe Family Irrevocable Trust", "", "Crabbe", "trust_title"),
    ("Boch Family Revocable Trust", "", "Boch", "trust_title"),
    # no human to trace
    ("Jas Holdings Llc", "", "", "entity_company"),
    ("Turnleaf Properties Llc", "", "", "entity_company"),
    ("Living Trust", "", "", "trust_title"),
    ("", "", "", "none"),
]


def main() -> int:
    bad = 0
    for title, first, last, source in CASES:
        got = parse_owner_blob(title)
        ok = (got["first"], got["last"], got["source"]) == (first, last, source)
        bad += not ok
        print(f"{'ok  ' if ok else 'FAIL'} {title!r:42} -> {got['first']!r} {got['last']!r} "
              f"[{got['source']}]" + ("" if ok else f"   want {first!r} {last!r} [{source}]"))
    print(f"\n{len(CASES) - bad}/{len(CASES)} pass")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
