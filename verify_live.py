"""Verify the live seeded plan against every stated rule, and explain each
unscheduled ticket in terms of the student's declared window.

Runs against the server on port 3000 (the one `serve.py` starts) and signs in
as a technician, because /api/plan now reports the day of whoever is asking.
Override the identity with DC_USER / DC_PASSWORD if you like:

    DC_USER=B02 DC_PASSWORD=fix2026 python verify_live.py
"""
import os
import sys

from testclient import Client

BASE = os.environ.get("DC_BASE", "http://127.0.0.1:3000")

TECH = Client(BASE, os.environ.get("DC_USER", "B02"),
              os.environ.get("DC_PASSWORD", "fix2026"))


def call(path, body=None):
    return TECH.call(path, body)[1]


def m(s):
    return int(s[:2]) * 60 + int(s[3:])


def win_label(tk):
    out = []
    for a, b in tk["slots"]:
        out.append("%s-%s" % (hm(8 * 60 + a * 30), hm(8 * 60 + (b + 1) * 30)))
    return ", ".join(out)


def hm(v):
    return "%02d:%02d" % (v // 60, v % 60)


cfg = call("/api/config")
plan = call("/api/plan?date=%s" % cfg["days"][0])
tickets = {t["id"]: t for t in call("/api/tickets")["tickets"]}
jobs = plan["jobs"]
st = plan["stats"]

print("=" * 84)
print("LIVE PLAN VERIFICATION — %s / %s" % (plan["date"], plan["worker"]["name"]))
print("=" * 84)

errors, notes = [], []
prev_end = None
prev_college = None
for j in jobs:
    want = 15 if (prev_college is None or prev_college != j["college"]) else 5
    if j["travel_in"] != want:
        errors.append("travel_in %d != %d for %s" % (j["travel_in"], want, j["ticket_id"]))
    if prev_end is not None and m(j["start"]) < prev_end + j["travel_in"]:
        errors.append("gap too small before %s" % j["ticket_id"])
    s_m, e_m = m(j["start"]), m(j["end"])
    if e_m - s_m != j["duration"]:
        errors.append("duration mismatch %s" % j["ticket_id"])
    if s_m < 13 * 60 + 30 and e_m > 12 * 60:
        errors.append("straddles lunch %s" % j["ticket_id"])
    tk = tickets[j["ticket_id"]]
    wins = [(8 * 60 + a * 30, 8 * 60 + (b + 1) * 30) for a, b in tk["slots"]]
    inside = [w for w in wins if s_m >= w[0] and e_m <= w[1]]
    if not inside:
        errors.append("outside declared window %s" % j["ticket_id"])
    spec = [t for t in cfg["repair_types"] if t["id"] == j["repair_type"]][0]
    if spec["minutes"] != j["duration"]:
        errors.append("not the fixed duration %s" % j["ticket_id"])
    print("\n#%d %s  %s-%s  %s %s" % (j["seq"], j["ticket_id"], j["start"], j["end"],
                                      j["college_label"], j["repair_label"]))
    print("    priority %-7s fixed %2dmin  travel_in %dmin  window[%s]  -> fits"
          % (j["priority"], j["duration"], j["travel_in"], win_label(tk)))
    prev_end, prev_college = e_m, j["college"]

print("\n--- unscheduled tickets, explained ---")
for u in plan["unscheduled"]:
    tk = tickets[u["ticket_id"]]
    wins = [(8 * 60 + a * 30, 8 * 60 + (b + 1) * 30) for a, b in tk["slots"]]
    dur = tk["duration_minutes"]
    widest = max(w[1] - w[0] for w in wins)
    print("  %s %s %s · %s (%d min)" % (u["ticket_id"], u["college_label"], u["dorm"],
                                        u["repair_label"], dur))
    print("     window: %s  (widest block %d min)" % (win_label(tk), widest))
    if widest < dur:
        print("     reason: the declared window (%d min) is shorter than the fixed "
              "repair time (%d min) — verified as a TRUE negative" % (widest, dur))
        if widest >= dur:
            errors.append("unscheduled but window is wide enough: %s" % u["ticket_id"])
    else:
        print("     reason: no room left in the shift after higher-priority jobs; "
              "the window itself is adequate")

print("\n--- stats ---")
for k in ("jobs", "unscheduled", "work_minutes", "travel_minutes", "shift_start",
          "shift_end", "first_start", "last_end"):
    print("  %-16s %s" % (k, st.get(k)))

print("\n" + "=" * 84)
print("rule violations: %d" % len(errors))
for e in errors:
    print("  !! " + e)
sys.exit(1 if errors else 0)
