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
# --- 8: the PR path (Basem 2026-09-30: PR fields, never a REL slot; PR role wins)
rels = [
    {"name": "WILLIAM BRENT", "canon_rel": "Son", "is_dm": True, "phones": [{"number": "7035550001"}]},
    {"name": "MARY BRENT", "canon_rel": "Daughter", "is_dm": True, "phones": []},
]
check("strict first+last picks the PR", m._pick_pr(rels, "William Brent") is rels[0])
check("nickname (loose) picks the PR when unambiguous", m._pick_pr(rels, "Bill Brent") is rels[0])
check("middle initial / suffix ignored", m._pick_pr(rels, "William K. Brent Jr") is rels[0])
check("no PR named -> None", m._pick_pr(rels, "") is None)
check("PR not among relatives -> None", m._pick_pr(rels, "Jason Dolphin") is None)
twins = rels + [{"name": "WILLIAM BRENT", "canon_rel": "Nephew", "is_dm": False, "phones": []}]
check("two strict matches = ambiguous = None", m._pick_pr(twins, "William Brent") is None)
flagged = [{"name": "X", "canon_rel": "Relative", "pr_on_record": "Jason B", "phones": []}] + rels
check("IDI-flagged PR wins over a name match", m._pick_pr(flagged, "William Brent") is flagged[0])
check("split: middle initial rides with the first name",
      m._split_pr_name("James K Heins") == ("James K", "Heins"))
check("split: periods stripped, Mc kept", m._split_pr_name("anthony l. mcKee") == ("Anthony L", "McKee"))
check("split: suffix stays with the surname", m._split_pr_name("John Smith Jr") == ("John", "Smith Jr"))
check("split: hyphen kept", m._split_pr_name("Jason Beardsley-Smith") == ("Jason", "Beardsley-Smith"))
check("split: single token", m._split_pr_name("Cher") == ("Cher", ""))

# Pr.{m} removal needs provenance: never on the tag alone (vendor imports share it)
owner_phones_pr = [
    {"number": "4438246430", "tags": ["Pr.1", "Dial Third"], "status": "UNKNOWN"},   # vendor's PR number
    {"number": "4438246431", "tags": ["Pr.2", "Dial Third"], "status": "UNKNOWN"},   # OUR prior plan's
]
st_pr, _, foreign_pr = m._stale_numbers(owner_phones_pr, set(), {"4438246431"})
check("Pr-tagged number in OUR prior plan comes off", st_pr == ["4438246431"])
check("Pr-tagged number not in the prior plan is foreign, kept", foreign_pr == ["4438246430"])
check("no prior plan: Pr-tagged numbers never removed", m._stale_numbers(owner_phones_pr, set(), None)[0] == [])
check("_PLAN_TAG_RE accepts Pr.N / Rel / Owner, rejects words",
      all(m._PLAN_TAG_RE.match(t) for t in ("Pr.1", "Rel3.2", "Owner.1"))
      and not any(m._PLAN_TAG_RE.match(t) for t in ("PR.1", "Son", "Dial First")))

# PR phone field ghost: blanked on provenance; PR NAME fields never
FP = fields_for("Personal Representative Phone", "PR First Name", "PR Last Name", "REL1: Full Name", "REL1: Phone 1")
live_pr = {"Personal Representative Phone": "+14438246431", "PR First Name": "Old", "PR Last Name": "Name"}
check("PR phone ghost blanked when its digits were removed",
      m._ghost_blanks(live_pr, {}, {"4438246431"}, FP) == ["Personal Representative Phone"])
check("PR phone kept when the plan writes the field",
      m._ghost_blanks(live_pr, {"Personal Representative Phone": "4438246431"}, {"4438246431"}, FP) == [])
check("PR phone kept without provenance", m._ghost_blanks(live_pr, {}, {"1112223333"}, FP) == [])

# trace month off the SmartSkip order id (Mongo ObjectId timestamp)
import json as _json, tempfile as _tf
_d = pathlib.Path(_tf.mkdtemp())
(_d / "smartskip_orders.json").write_text(_json.dumps([
    {"bulkSkipId": "6abc1b10fdd63d3dd51e2a98", "paid": False},
    {"bulkSkipId": "6abc220dac2012da73ce654d", "paid": True}]), encoding="utf-8")
check("trace month = the PAID order's month (2026-09-29)", m._trace_month(_d) == "09/2026")
(_d / "smartskip_orders.json").write_text("[]", encoding="utf-8")
check("no order -> empty", m._trace_month(_d) == "")
check("month tag format guard", m._MONTH_TAG_RE.match("09/2026") and not m._MONTH_TAG_RE.match("9/2026"))

# --- 9: the create path (records not on the account, 2026-10-01)
plan_rec = {"street": "10011 Tenbrook Drive", "city": "Silver Spring", "state": "MD", "zip": "20901",
            "owner_first": "Tracey M.", "owner_last": "Shaw", "business_name": "",
            "mail_street": "", "mail_city": "", "mail_state": "", "mail_zip": ""}
b = m._create_body(plan_rec)
check("create body = address + owner only", set(b) == {"address", "owner"})
check("no mailing on file -> property address is the mailing", b["owner"]["address"] == b["address"])
check("period stripped from the owner first name (Incomplete trap)", b["owner"]["first_name"] == "Tracey M")
check("person owner carries no company key", "company" not in b["owner"])
ent = m._create_body({**plan_rec, "owner_first": "", "owner_last": "", "business_name": "SHAW FAMILY TRUST"})
check("entity owner = company, person keys OMITTED", ent["owner"].get("company") == "SHAW FAMILY TRUST"
      and "first_name" not in ent["owner"] and "last_name" not in ent["owner"])
check("no owner at all -> None", m._create_body({**plan_rec, "owner_first": "", "owner_last": "", "business_name": ""}) is None)
check("no street -> None", m._create_body({**plan_rec, "street": ""}) is None)
check("Probate pairs with Register of Wills Probates (MD)",
      m._lists_for_create({"lists": "Probate", "state": "MD"}) == ["Probate", "Register of Wills Probates"])
check("VA gets no Register of Wills list", m._lists_for_create({"lists": "Probate", "state": "VA"}) == ["Probate"])
check("Inheritance stays as is", m._lists_for_create({"lists": "Inheritance", "state": "MD"}) == ["Inheritance"])
check("already paired -> no duplicate",
      m._lists_for_create({"lists": "Register of Wills Probates, Probate", "state": "DC"}) == ["Register of Wills Probates", "Probate"])
check("no record -> no lists", m._lists_for_create(None) == [])
check("201 + uuid -> created", m._created_uuid(201, {"uuid": "abc"}) == ("abc", "created"))
check("400 already exists -> the EXISTING uuid, 'exists'",
      m._created_uuid(400, {"non_field_errors": ["Property address already exists!"], "property": ["e1"]}) == ("e1", "exists"))
check("400 validation -> no uuid", m._created_uuid(400, {"owner": ["This field is required."]})[0] == "")
check("400 already exists as a STRING body (what WriteApi returns) -> uuid read",
      m._created_uuid(400, '{"non_field_errors":["Property address already exists!"],"property":["74742f8e-x"]}') == ("74742f8e-x", "exists"))
check("403 -> no uuid", m._created_uuid(403, "forbidden")[0] == "")
check("200 without uuid -> no uuid", m._created_uuid(200, {})[0] == "")

print("\nALL PASS" if not FAILS else f"\n{FAILS} FAILED")
sys.exit(1 if FAILS else 0)
