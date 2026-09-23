"""Offline checks for src/scripts/probe_property_create.py. No network, no account.

Run after ANY change to classify / owner_for_create / cmd_probe. Each check is a
failure mode this repo has already paid for on a different endpoint:

  1. A 400 naming the duplicate is the PASS -- the route works and nothing was made.
  2. A 403 is a real answer (BLOCKED), not an error to retry.
  3. A 429/0/5xx is NOT evidence either way -- probate_list_restore learned that a
     throttled read collapsed into a verdict is how a no-op looks like a change.
  4. A 2xx returning the uuid we targeted is an upsert of our own data: no change.
  5. A 2xx returning a NEW uuid means we created a record that cannot be deleted
     over this API -- it must alarm, never report success.
  6. A 2xx with no uuid at all is also an alarm (the notes/pin "200 that lies" shape).
  7. The probe body carries address + owner and NOTHING else -- no tags, no lists,
     no notes -- so an unexpected upsert is a no-op instead of a pollution event.
  8. An entity owner sends `company` and OMITS the person keys; a blank first_name
     is rejected outright by the API and "" is not the same as omitting the key.
  9. A record with no usable owner is refused as a probe target rather than sent
     with a blank name.
 10. The count check fires: a clean-looking response with a moved count is an alarm.

  python tools/manual/test_property_create_probe.py
"""
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "src"), str(ROOT / "src" / "scripts")]

import probe_property_create as m  # noqa: E402

FAILS = 0


def check(label, cond):
    global FAILS
    print(("ok   " if cond else "FAIL ") + label)
    if not cond:
        FAILS += 1


TARGET = "11111111-2222-3333-4444-555555555555"
OTHER = "99999999-8888-7777-6666-555555555555"
DUP = {"non_field_errors": ["Property address already exists!"], "property": [TARGET]}


# --- 1: the duplicate 400 is the pass
v = m.classify(400, DUP, TARGET)
check("400 duplicate -> ROUTE_WORKS, exit 0, nothing created",
      v["verdict"] == "ROUTE_WORKS" and v["exit"] == 0 and not v["created_uuid"])

# the same body arriving as raw text (WriteApi hands back text on an error)
v = m.classify(400, json.dumps(DUP), TARGET)
check("400 duplicate as raw TEXT parses the same", v["verdict"] == "ROUTE_WORKS")

# a duplicate naming someone else still proves the route, but says so
v = m.classify(400, {"non_field_errors": ["Property address already exists!"],
                     "property": [OTHER]}, TARGET)
check("400 duplicate naming another uuid -> ROUTE_WORKS but flagged",
      v["verdict"] == "ROUTE_WORKS" and "not the target" in v["note"])

# --- 2: 403 is an answer, not an error
v = m.classify(403, "forbidden", TARGET)
check("403 -> BLOCKED, exit 3", v["verdict"] == "BLOCKED" and v["exit"] == 3)

# --- 3: transient statuses are never evidence
for st in (0, 429, 500, 502, 503):
    v = m.classify(st, "", TARGET)
    check("status %s -> TRANSIENT, inconclusive" % st,
          v["verdict"] == "TRANSIENT" and v["exit"] == 4)

# --- 4: upsert of our own data changes nothing
v = m.classify(200, {"uuid": TARGET}, TARGET)
check("2xx echoing the target uuid -> UPSERT_NO_CHANGE, exit 0",
      v["verdict"] == "UPSERT_NO_CHANGE" and v["exit"] == 0 and not v["created_uuid"])

# --- 5: a new uuid is the one bad outcome
for st in (200, 201, 202):
    v = m.classify(st, {"uuid": OTHER}, TARGET)
    check("%s with a NEW uuid -> RECORD_CREATED, exit 5, uuid captured" % st,
          v["verdict"] == "RECORD_CREATED" and v["exit"] == 5
          and v["created_uuid"] == OTHER)

# --- 6: a 2xx with no uuid is also an alarm, not a pass
v = m.classify(201, {"detail": "ok"}, TARGET)
check("2xx with no uuid -> UNKNOWN_2XX, exit 5 (assume written)",
      v["verdict"] == "UNKNOWN_2XX" and v["exit"] == 5)

# a non-duplicate 400 is a body problem, not a verdict on the route
v = m.classify(400, {"owner": {"first_name": ["This field may not be blank."]}}, TARGET)
check("400 validation error -> VALIDATION_ERROR, inconclusive",
      v["verdict"] == "VALIDATION_ERROR" and v["exit"] == 4)

v = m.classify(401, "", TARGET)
check("401 -> AUTH_FAILED, inconclusive (never BLOCKED)",
      v["verdict"] == "AUTH_FAILED" and v["exit"] == 4)


# ------------------------------------------------------- owner / body shaping
def rec(owner, street="12 Elm St", city="Rockville", state="MD", zp="20850"):
    return {"uuid": TARGET,
            "address": {"street": street, "city": city, "state": state, "postal_code": zp},
            "owner": owner}


# --- 8: person vs entity
o = m.owner_for_create(rec({"first_name": "Jane", "last_name": "Doe",
                            "address": {"street": "9 Oak Ave", "city": "Bowie",
                                        "state": "MD", "postal_code": "20715"}}))
check("person owner -> first+last, no company key",
      o["first_name"] == "Jane" and o["last_name"] == "Doe" and "company" not in o)

o = m.owner_for_create(rec({"first_name": "", "last_name": "",
                            "company": "ACME HOLDINGS LLC", "address": {}}))
check("entity owner -> company only, person keys OMITTED (not blank)",
      o.get("company") == "ACME HOLDINGS LLC"
      and "first_name" not in o and "last_name" not in o)

o = m.owner_for_create(rec({"first_name": "Solo", "last_name": "", "address": {}}))
check("first name with no surname -> no last_name key", "last_name" not in o)

o = m.owner_for_create(rec({"first_name": "Jane", "last_name": "Doe", "address": {}}))
check("empty mailing falls back to the property address",
      o["address"]["street"] == "12 Elm St" and o["address"]["postal_code"] == "20850")

# --- 9: no usable owner -> refuse rather than send a blank name
check("blank owner -> None (unusable as a probe target)",
      m.owner_for_create(rec({"first_name": "", "last_name": "", "company": ""})) is None)
check("missing owner block -> None",
      m.owner_for_create({"uuid": TARGET, "address": {"street": "x"}}) is None)


# --- 7 + 10: the live probe path, driven through a fake client
class FakeApi:
    """Returns (status, body) exactly as WriteApi does. Records what was sent."""

    def __init__(self, post=(400, DUP), counts=(3, 3)):
        self.post = post
        self.counts = list(counts)
        self.sent = []

    def get(self, path):
        if path.endswith("ordering=-created"):
            return 200, {"results": [{"uuid": TARGET}]}
        return 200, rec({"first_name": "Jane", "last_name": "Doe",
                         "address": {"street": "9 Oak Ave", "city": "Bowie",
                                     "state": "MD", "postal_code": "20715"}})

    def search(self, body):
        return 200, {"count": self.counts.pop(0)}

    def write(self, method, path, body, tries=4):
        self.sent.append((method, path, body))
        return self.post


api = FakeApi()
code = m.cmd_probe(api, "")
method, path, body = api.sent[0]
check("probe POSTs the create route once", len(api.sent) == 1
      and method == "POST" and path == "/api/internal/property/")
check("probe body is address+owner ONLY -- no tags/lists/notes/custom fields",
      set(body) == {"address", "owner"})
check("probe body carries the record's own street",
      body["address"]["street"] == "12 Elm St")
check("duplicate response -> exit 0", code == 0)

api = FakeApi(post=(403, "forbidden"))
check("403 through cmd_probe -> exit 3", m.cmd_probe(api, "") == 3)

# --- 10: a clean-looking response whose count moved is an alarm
api = FakeApi(post=(400, DUP), counts=(3, 4))
check("count moved 3 -> 4 under a clean 400 -> exit 5 (COUNT_MOVED)",
      m.cmd_probe(api, "") == 5)

# a failed pre-count aborts rather than probing blind
class NoCount(FakeApi):
    def search(self, body):
        return 429, {}


check("pre-count failure aborts before the POST", m.cmd_probe(NoCount(), "") == 4
      and not NoCount().sent)

print("\n%d failure(s)" % FAILS if FAILS else "\nall checks passed")
sys.exit(1 if FAILS else 0)
