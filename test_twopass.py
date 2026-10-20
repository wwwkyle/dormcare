"""Direct test of the two-pass scheduler against the full demo workload.

Shows that pass 2 recovers tickets pass 1 had to skip, and re-verifies every
hard rule on the merged result.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from scheduler import schedule_day

DAY = "2026-09-22"
TICKETS = [
    ("T1", "harmonia",   "宿舍 2 栋", "C-410", "plumbing", "urgent", [[1, 6]],  "下水道堵塞"),
    ("T2", "shaw",       "东座 1 栋", "A-301", "ac",       "urgent", [[2, 8]],  "空调漏水不制冷"),
    ("T3", "minerva",    "南区",      "E-508", "network",  "high",   [[7, 15]], "网口不通"),
    ("T4", "muse",       "",          "B-205", "light",    "normal", [[6, 16]], "灯管不亮"),
    ("T5", "shaw",       "西座 2 栋", "A-415", "desk",     "low",    [[5, 14]], "抽屉卡住"),
    ("T6", "muse",       "",          "B-102", "chair",    "high",   [[4, 10]], "气压杆坏"),
    ("T7", "diligentia", "A 座",      "F-303", "wardrobe", "normal", [[2, 9]],  "柜门掉落"),
    ("T8", "ling",       "C 座",      "D-201", "bed",      "normal", [[3, 12]], "床板裂缝"),
    ("T9", "eighth",     "北区",      "G-101", "door",     "normal", [[11, 13]], "门锁卡死"),
]

tickets = [{"id": i, "college": c, "block": b, "dorm": d, "repair_type": r,
            "priority": p, "date": DAY, "slots": s, "description": desc,
            "created_at": "%sT09:0%d:00" % (DAY, n)}
           for n, (i, c, b, d, r, p, s, desc) in enumerate(TICKETS)]

jobs, uns, st = schedule_day(DAY, tickets, None, "zh")

print("=" * 92)
print("TWO-PASS SCHEDULER — %d tickets in" % len(TICKETS))
print("=" * 92)
for j in jobs:
    print("#%d %-3s %s-%s  %-12s %-14s fixed %2dmin  in %2d  out %2d  %s"
          % (j["seq"], j["ticket_id"], j["start"].strftime("%H:%M"),
             j["end"].strftime("%H:%M"), j["college_label"], j["repair_label"],
             j["duration"], j["travel_in"], j["travel_out"], j["priority"]))

print("\nunscheduled: %d" % len(uns))
for t, r in uns:
    print("  %s %s (%s)" % (t["id"], t["repair_label"] if "repair_label" in t else t["repair_type"], r))

print("\nstats:", {k: st[k] for k in ("jobs", "unscheduled", "work_minutes",
                                      "travel_minutes", "first_start", "last_end",
                                      "span_minutes", "idle_minutes")})

# ---------------- invariant re-check on the merged result ---------------- #
print("\n" + "=" * 92)
print("INVARIANTS ON THE MERGED PLAN")
print("=" * 92)
m = lambda s: int(s.strftime("%H:%M")[:2]) * 60 + int(s.strftime("%H:%M")[3:])
errors = []
prev_end, prev_col = None, None
for j in jobs:
    s_m, e_m = m(j["start"]), m(j["end"])
    want = 15 if (prev_col is None or prev_col != j["college"]) else 5
    if j["travel_in"] != want:
        errors.append("travel_in %d != %d @%s" % (j["travel_in"], want, j["ticket_id"]))
    if prev_end is not None and s_m < prev_end + j["travel_in"]:
        errors.append("overlap before %s" % j["ticket_id"])
    if e_m - s_m != j["duration"]:
        errors.append("duration @%s" % j["ticket_id"])
    if s_m < 13 * 60 + 30 and e_m > 12 * 60:
        errors.append("lunch @%s %s-%s" % (j["ticket_id"], j["start"].strftime("%H:%M"), j["end"].strftime("%H:%M")))
    if s_m < 8 * 60 + 30 or e_m > 18 * 60:
        errors.append("shift @%s" % j["ticket_id"])
    tk = [t for t in tickets if t["id"] == j["ticket_id"]][0]
    wins = [(8 * 60 + a * 30, 8 * 60 + (b + 1) * 30) for a, b in tk["slots"]]
    if not any(s_m >= w[0] and e_m <= w[1] for w in wins):
        errors.append("window @%s" % j["ticket_id"])
    # no two jobs may overlap
    prev_end, prev_col = e_m, j["college"]

print("invariant failures: %d" % len(errors))
for e in errors:
    print("  !! " + e)

# determinism
jobs2, _, _ = schedule_day(DAY, tickets, None, "zh")
same = [(j["ticket_id"], j["start"], j["end"]) for j in jobs] == \
       [(j["ticket_id"], j["start"], j["end"]) for j in jobs2]
print("deterministic: %s" % same)
if not same:
    errors.append("not deterministic")

print("\nRESULT: %s  (%d of %d tickets scheduled)"
      % ("ALL GOOD" if not errors else "PROBLEMS", len(jobs), len(TICKETS)))
sys.exit(1 if errors else 0)
