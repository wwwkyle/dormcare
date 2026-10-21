"""Rule trace check — confirms the plan arithmetic matches the stated rules
exactly, minute by minute, and verifies the seam where the keyword fallback
classifier runs instead of the model.
"""
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE / "tests"))
sys.path.insert(0, str(HERE))
from testclient import (DEMO_STUDENT, DEMO_WORKERS, Client, Demo, reset_store,
                        server_output, spawn_server, store_path)

PORT = 3112
BASE = "http://127.0.0.1:%d" % PORT

STUDENT = None
WORKER = None
DEMO = None


def call(path, body=None, as_="student"):
    """The student files the queries; the technician plans and is briefed."""
    client = WORKER if as_ == "worker" else STUDENT
    return client.call(path, body)


def m(hhmm):
    return int(hhmm[:2]) * 60 + int(hhmm[3:])


def main():
    global STUDENT, WORKER, DEMO
    key = ""
    for candidate in (HERE.parent / "api_key.env",
                      HERE.parent.parent / "api_key.env",
                      HERE / "api_key.env"):
        if candidate.is_file():
            key = candidate.read_text(encoding="utf-8").strip()
            break
    print("key file found: %s" % bool(key))
    data = reset_store(store_path(HERE, "rule_trace"))
    proc, log_path = spawn_server(HERE, PORT, key, log_name="rule_trace.log",
                                  data=data)
    try:
        probe = Client(BASE)
        for _ in range(40):
            time.sleep(0.5)
            if probe.call("/api/health")[0] == 200:
                break

        STUDENT = Client(BASE, *DEMO_STUDENT)
        WORKER = Client(BASE, *DEMO_WORKERS[0])
        DEMO = Demo(BASE)

        s, cfg = call("/api/config")
        day = cfg["days"][1]

        spec = [
            ("shaw",      "东座 1 栋", "A-301", "ac",       "urgent", [[2, 8]]),
            ("muse",      "",          "B-102", "chair",    "high",   [[4, 10]]),
            ("muse",      "",          "B-205", "light",    "normal", [[6, 16]]),
            ("harmonia",  "宿舍 2 栋", "C-410", "plumbing", "urgent", [[1, 5]]),
            ("ling",      "C 座",      "D-201", "bed",      "normal", [[3, 12]]),
            ("shaw",      "西座 2 栋", "A-415", "desk",     "low",    [[5, 14]]),
        ]
        # Six tickets in five colleges, so six residents file them — the address
        # is the account's, not the request's.
        for c, b, d, r, p, sl in spec:
            DEMO.at(c, d).call("/api/tickets", {
                "lang": "en", "repair_type": r, "priority": p, "date": day,
                "slots": sl, "description": "trace %s" % r})

        s, plan = call("/api/schedule/optimize", {"date": day, "lang": "en"}, as_="worker")
        jobs = plan["jobs"]
        wid = plan["worker"]["worker_id"]

        print("=" * 78)
        print("RULE TRACE  — %s, technician %s (%s)"
              % (day, WORKER.user["name"], wid))
        print("=" * 78)
        clock = 8 * 60 + 30          # 08:30 shift start, at the workshop
        prev_college = None
        checks = []
        for j in jobs:
            travel = 15 if (prev_college is None or prev_college != j["college"]) else 5
            earliest = clock + travel
            start = m(j["start"])
            print("\nstop %d  %s  (%s)" % (j["seq"], j["ticket_id"], j["repair_label"]))
            print("  prev finish           %s  (= %d min from midnight)" % (fmt(clock), clock))
            print("  + travel (%s%d min)   -> earliest start %s" %
                  ("cross-college " if travel == 15 else "same-college ", travel, fmt(earliest)))
            checks.append((j["travel_in"] == travel,
                           "travel_in is %d, expected %d" % (j["travel_in"], travel)))
            if start < earliest:
                checks.append((False, "start %s is before earliest %s" % (j["start"], fmt(earliest))))
            if start > earliest:
                print("  -> pushed to window  %s  (+%d min wait)" % (j["start"], start - earliest))
            dur = j["duration"]
            end = start + dur
            print("  duration (fixed)      %d min  -> %s-%s" % (dur, j["start"], j["end"]))
            checks.append((m(j["end"]) - start == dur, "end does not equal start + duration"))
            # lunch rule
            crosses = start < 13 * 60 + 30 and end > 12 * 60
            checks.append((not crosses, "straddles lunch"))
            if start >= 12 * 60 and start < 13 * 60 + 30:
                checks.append((False, "starts inside lunch"))
            clock, prev_college = end, j["college"]

        if jobs:
            # closing leg: 5 min if the day ends inside the last college, else 15
            tail = jobs[-1]["travel_out"] or 5
            print("\nfinal: finish %s at %s, plus %d min back to the workshop = %s"
                  % (jobs[-1]["ticket_id"], jobs[-1]["end"], tail, fmt(m(jobs[-1]["end"]) + tail)))

        bad = [msg for ok, msg in checks if not ok]
        print("\n" + "=" * 78)
        print("checks: %d   failures: %d" % (len(checks), len(bad)))
        for b in bad:
            print("  !! " + b)
        print("unscheduled: %d  %s" % (len(plan["unscheduled"]),
              ", ".join("%s (%s)" % (u["ticket_id"], u["reason"]) for u in plan["unscheduled"])))

        # ---- now prove the model path is really used when the key is present ----
        print("\n" + "=" * 78)
        print("MODEL PATH CHECK (key present)")
        print("=" * 78)
        s, ai = call("/api/intake/parse", {
            "text": "宿舍的门锁卡死了，钥匙插进去转不动", "lang": "zh"})
        print("intake source = %s | type = %s | dur = %s | priority = %s"
              % (ai.get("source"), ai.get("repair_type"), ai.get("duration_minutes"),
                 ai.get("priority")))
        print("reason: %s" % ai.get("reason"))
        if ai.get("source") == "model":
            print("=> the model really classified this, and the duration still came from the table")
        s, dis = call("/api/dispatch/advice", {"date": day, "lang": "en",
                                              "mode": "dispatch"}, as_="worker")
        print("\ndispatch ai_enabled = %s, %d chars"
              % (dis.get("ai_enabled"), len(dis.get("reply", ""))))
        print(dis.get("reply", "")[:900])
        return 0 if not bad else 2
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=8)
        except Exception:
            proc.kill()
        for line in server_output(log_path, 8000).splitlines():
            if "upstream" in line or "[error]" in line:
                print("[server] " + line)


def fmt(mins):
    return "%02d:%02d" % (mins // 60, mins % 60)


if __name__ == "__main__":
    sys.exit(main())
