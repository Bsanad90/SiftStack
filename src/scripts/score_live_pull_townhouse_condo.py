"""Top 100 Opportunities for the Townhouse + Condo segment, live account pull.

Same 4 Pillars engine, same gates, as score_live_pull.py -- only the
structure_type allowlist changes. Split out as its own list per Ty's request
rather than merged into the single-family Top 100, since townhouse/condo
deals price and exit differently (HOA, smaller lot, different buyer pool).

"Condominiums (Industrial)" is included: verified live against sample
records (1-3 bed, 1-2 bath, $250K-$900K, "Apt"/"Unit" addresses) -- it is
this dataset's assessor tax-class label for a residential condo unit, not an
actual industrial property, unlike the buildings that had to be gated out of
the single-family list.

Usage:
    python src/scripts/score_live_pull_townhouse_condo.py [input_json] [output_xlsx]
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import score_live_pull as base  # noqa: E402

TOWNHOUSE_CONDO_ALLOWED = {
    "townhouse (residential)", "townhouse",
    "condominium unit (residential)", "condominiums (industrial)",
}


def main():
    in_path = sys.argv[1] if len(sys.argv) > 1 else "output/live_account_pull.json"
    out_path = sys.argv[2] if len(sys.argv) > 2 else "output/top_100_townhouse_condo_live.xlsx"
    base.run(in_path, out_path, allowed_types=TOWNHOUSE_CONDO_ALLOWED,
             segment_label="Townhouse + Condo")


if __name__ == "__main__":
    main()
