"""Offline check of the DP pusher's re-push rules. No network.

Run after ANY change to _ghost_blanks / _ordered_keys / _stale_board_messages in
src/scripts/obituary_dp_batch.py, the way test_md_row_pass_budgets.py guards the RoW pull.

What it protects (all measured live):

  1. A dropped relative's `REL{n}: Phone k` ghosts are blanked only when their digits are
     a number THIS push removed under our own tag (2026-09-11 probe, 6637 Frederick Rd).
  2. The slot's `REL{n}: Full Name` goes too -- but only when every phone field of the
     slot was ours (2026-09-15: 200 name ghosts survived the phone-only rule).
  3. A slot still holding a number we did not remove is another pusher's: never touched
     (2026-09-10 rule).
  4. `--keys-file` order is preserved and unknown keys are dropped, never guessed.
  5. Same-family board matching is equality, not prefix (the 2026-09-01 probate wipe).

  python tools/manual/test_dp_push_ghosts.py
"""
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "src"), str(ROOT / "src" / "scripts")]

import obituary_dp_batch as m  # noqa: E402

FAILS = 0


def check(label, cond):
    global FAILS
    print(("ok   " if cond else "FAIL ") + label)
    if not cond:
        FAILS += 1


def fields_for(*labels):
    return {l: {"uuid": f"uuid-{l}"} for l in labels}


F = fields_for(*[f"REL{n}: Full Name" for n in range(1, 8)],
               *[f"REL{n}: Phone {k}" for n in range(1, 8) for k in (1, 2, 3)])

# --- 1+2: slot fully ours -> phones AND name blanked; slot re-used by the plan -> name kept
live = {
    "REL1: Full Name": "WAYNE HILL", "REL1: Phone 1": "4105550001", "REL1: Phone 2": "4105550002",
    "REL5: Full Name": "IN LAW", "REL5: Phone 1": "4105550005", "REL5: Phone 2": "",
}
plan = {"REL1: Full Name": "SARAH LIBSON", "REL1: Phone 1": "7035550009"}
removed = {"4105550002", "4105550005"}
out = m._ghost_blanks(live, plan, removed, F)
check("REL1 Phone 2 ghost blanked (digits removed, not in plan)", "REL1: Phone 2" in out)
check("REL1 Full Name kept (plan re-uses the slot)", "REL1: Full Name" not in out)
check("REL1 Phone 1 untouched (plan writes it)", "REL1: Phone 1" not in out)
check("REL5 phone blanked", "REL5: Phone 1" in out)
check("REL5 Full Name blanked (slot held only our numbers)", "REL5: Full Name" in out)

# --- 3: slot with one removed number and one foreign number -> phone blanked, name kept
live = {"REL6: Full Name": "SHARED SLOT", "REL6: Phone 1": "4105550006", "REL6: Phone 2": "2025551234"}
out = m._ghost_blanks(live, {}, {"4105550006"}, F)
check("REL6 Phone 1 blanked", out == ["REL6: Phone 1"] or "REL6: Phone 1" in out)
check("REL6 Full Name kept (foreign number remains)", "REL6: Full Name" not in out)

# --- no provenance -> nothing
live = {"REL7: Full Name": "OTHER PUSHER", "REL7: Phone 1": "", "REL7: Phone 2": ""}
check("slot with no removed number untouched", m._ghost_blanks(live, {}, set(), F) == [])
check("nothing removed -> nothing blanked",
      m._ghost_blanks({"REL2: Phone 1": "4105550002"}, {}, set(), F) == [])

# --- label the account lacks is never written
live = {"REL3: Full Name": "X", "REL3: Phone 1": "4105550003"}
out = m._ghost_blanks(live, {}, {"4105550003"}, fields_for("REL3: Phone 1"))
check("missing Full Name field label never written", out == ["REL3: Phone 1"])

# --- +1 prefix on the live value still matches
live = {"REL4: Full Name": "PLUS ONE", "REL4: Phone 1": "+14105550004"}
out = m._ghost_blanks(live, {}, {"4105550004"}, F)
check("+1-prefixed live value matches removed digits", set(out) == {"REL4: Phone 1", "REL4: Full Name"})

# --- 4: keys-file ordering
check("ordered subset keeps file order, drops unknown",
      m._ordered_keys(["a", "b", "c"], ["c", "zzz", "a"]) == ["c", "a"])
check("empty keys file pushes nothing", m._ordered_keys(["a"], []) == [])

# --- 5: same-family equality
dp = "DEEP PROSPECTING 2026-09-01 (SmartSkip + obituary + Trestle)\nline"
board = [{"uuid": "1", "message": "DEEP PROSPECTING 2026-09-11 (x)\n..", "pinned": True},
         {"uuid": "2", "message": "PROBATE - Register of Wills Anne Arundel County (loaded 2026-09-01)"},
         {"uuid": "3", "message": "DEEP PROSPECTING REVIEW 2026-09-15"}]
same = m._stale_board_messages(board, dp, "")
check("same family = equality: only the DP note", [x["uuid"] for x in same] == ["1"])
check("new uuid excluded", m._stale_board_messages(board, dp, "1") == [])


# --- 6: phone removal provenance (2026-09-17, Leisure World #405)
owner_phones = [
    {"number": "4254789568", "tags": ["Rel1.1", "Dial First"], "status": "UNKNOWN"},   # ours, still planned
    {"number": "4252992646", "tags": ["Rel3.1", "Dial First"], "status": "DEAD"},      # ours, dropped
    {"number": "4438242865", "tags": ["Rel1.1", "Dial Third"], "status": "UNKNOWN"},   # RFD's, same tag vocabulary
    {"number": "4438246430", "tags": ["PR.1", "Dial Third"], "status": "UNKNOWN"},     # RFD's PR number
    {"number": "3015550000", "tags": ["Dial Second"], "status": "UNKNOWN"},           # untagged skip-trace number
]
want = {"4254789568"}
prior = {"4254789568", "4252992646"}
stale, restore, foreign = m._stale_numbers(owner_phones, want, prior)
check("prior plan: only our dropped number comes off", stale == ["4252992646"])
check("prior plan: RFD's Rel-tagged number kept and reported", foreign == ["4438242865"])
check("restore payload keeps the live disposition", restore[0]["status"] == "DEAD")
stale0, _, foreign0 = m._stale_numbers(owner_phones, want, None)
check("no prior plan = old behaviour (tag alone), nothing reported foreign",
      set(stale0) == {"4252992646", "4438242865"} and foreign0 == [])
check("PR-tagged and untagged numbers never touched either way",
      "4438246430" not in stale0 and "3015550000" not in stale0)
check("empty prior (first push of this key) removes nothing",
      m._stale_numbers(owner_phones, want, set())[0] == [])

# --- 7: a prior-plan number the new plan dropped clears its REL field even if it was never on the owner
live = {"REL7: Full Name": "OVER THE CAP", "REL7: Phone 1": "+13015559999"}
prior_only = {"3015559999"}          # in our prior plan, never landed on the owner (30-cap)
check("prior-plan-only number counts as provenance for REL ghosts",
      set(m._ghost_blanks(live, {}, prior_only, F)) == {"REL7: Phone 1", "REL7: Full Name"})
print("\nALL PASS" if not FAILS else f"\n{FAILS} FAILED")
sys.exit(1 if FAILS else 0)
