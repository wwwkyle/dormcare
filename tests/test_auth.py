#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
DormCare · 权限边界测试 / Authorisation boundary test
=====================================================

The whole point of adding accounts was to stop the browser deciding who sees
what. This file is the evidence that it worked, and it is deliberately written
as attacks rather than as happy paths:

    1. anonymous callers get nothing
    2. a student sees exactly their own tickets — and not one row more
    3. a technician sees exactly the jobs the engine assigned to them
    4. neither can touch a row that belongs to somebody else (IDOR)
    5. the body/query `worker` parameter cannot be used to impersonate
    6. role-only endpoints refuse the other role
    7. session handling: HttpOnly cookie, logout really revokes
    8. sign-up rules, and a brute-force budget per (caller, account)
    9. the ADDRESS is the account's, not the request's — a student cannot file
       a repair against somebody else's building, and the only way to name a
       different place is to declare the job a shared-area repair

Run it against a live server:

    python tests/test_auth.py      # starts its own server on port 3112
"""

import json
import sys
import time
from pathlib import Path

from testclient import (DEMO_STUDENT, DEMO_STUDENT_2, DEMO_WORKERS, Client,
                        reset_store, server_output, spawn_server, store_path)

HERE = Path(__file__).resolve().parents[1]
PORT = 3112
BASE = "http://127.0.0.1:%d" % PORT

FAILS = []
PASSES = []


def check(label, ok, detail=""):
    (PASSES if ok else FAILS).append(label)
    print(("PASS  " if ok else "FAIL  ") + label + (("  -> " + str(detail)) if detail else ""))


def main():
    key = ""
    for c in (HERE / "api_key.env", HERE.parent / "api_key.env"):
        if c.is_file():
            key = c.read_text(encoding="utf-8").strip()
            break

    data = reset_store(store_path(HERE, "auth"))
    proc, log_path = spawn_server(HERE, PORT, key, log_name="test_auth.log",
                                  data=data, env_extra={
                                      "DORMCARE_PUBLIC_ORIGINS": "https://dormcare.example",
                                      "DORMCARE_TRUST_PROXY": "1",
                                  })
    try:
        anon = Client(BASE)
        for _ in range(40):
            time.sleep(0.5)
            if anon.call("/api/health")[0] == 200:
                break
        else:
            print("FAIL  server did not start")
            print(server_output(log_path))
            return 1

        # ---------------------------------------------------------------- #
        # 1. anonymous access                                              #
        # ---------------------------------------------------------------- #
        for path, body, method in [
            ("/api/tickets", None, "GET"),
            ("/api/workers", None, "GET"),
            ("/api/plan?date=2026-10-07", None, "GET"),
            ("/api/setup", {"key": "sk-" + "x" * 30}, "POST"),
            ("/api/tickets", {"college": "shaw", "dorm": "A-1",
                              "date": "2026-10-07", "slots": [[1, 2]]}, "POST"),
            ("/api/schedule/optimize", {"date": "2026-10-07"}, "POST"),
            ("/api/intake/parse", {"text": "灯坏了"}, "POST"),
            ("/api/tickets/offroute", {"id": "T00000000", "action": "take"}, "POST"),
            ("/api/tickets/reschedule", {"id": "T00000000", "accept": True}, "POST"),
            ("/api/account/room", {"college": "shaw", "dorm": "A-1"}, "POST"),
            ("/api/account/password", {"current_password": "x", "password": "abcdef"}, "POST"),
        ]:
            s, d = anon.call(path, body, method)
            check("anonymous %-24s -> 401" % path.split("?")[0],
                  s == 401, "%d %s" % (s, d.get("code")))

        s, d = anon.call("/api/auth/me")
        check("anonymous /api/auth/me reports not signed in",
              s == 401 and d.get("authenticated") is False, d)

        # the two things an anonymous visitor legitimately needs
        check("anonymous may read /api/health", anon.call("/api/health")[0] == 200)
        cfg_status, cfg = anon.call("/api/config")
        check("anonymous may read /api/config (no personal data)",
              cfg_status == 200 and len(cfg.get("colleges", [])) == 8
              and "phone" not in json.dumps(cfg))

        # ---------------------------------------------------------------- #
        # 2. sign-in rules                                                 #
        # ---------------------------------------------------------------- #
        bad = Client(BASE)
        s, d = bad.call("/api/auth/login", {"username": DEMO_STUDENT[0], "password": "wrong-one"})
        check("wrong password -> 401", s == 401 and d.get("code") == "auth_bad_credentials", d)
        s, d = bad.call("/api/auth/login", {"username": "no-such-account", "password": "whatever"})
        check("unknown account -> same 401 (no account enumeration)",
              s == 401 and d.get("code") == "auth_bad_credentials", d)

        stu = Client(BASE)
        s, d = stu.call("/api/auth/login", {"username": DEMO_STUDENT[0], "password": DEMO_STUDENT[1]})
        stu.user = d.get("user")            # keep the client's view in sync
        check("student signs in", s == 200 and d["user"]["role"] == "student", d.get("user", {}))
        # Signing in opens the ONE main menu; the console it leads to is still
        # decided per role (see the page-gate checks further down).
        check("student lands on the main menu", d.get("home") == "/menu.html", d.get("home"))
        cookie = stu.last_set_cookie
        check("session cookie is HttpOnly + SameSite=Lax",
              "HttpOnly" in cookie and "SameSite=Lax" in cookie, cookie)

        tech = Client(BASE)
        s, d = tech.call("/api/auth/login", {"username": DEMO_WORKERS[0][0], "password": DEMO_WORKERS[0][1]})
        tech.user = d.get("user")
        check("technician signs in", s == 200 and d["user"]["role"] == "worker", d.get("user", {}))
        check("technician lands on the main menu too",
              d.get("home") == "/menu.html", d.get("home"))

        tech2 = Client(BASE, *DEMO_WORKERS[1])
        tech3 = Client(BASE, *DEMO_WORKERS[2])
        stu2 = Client(BASE, *DEMO_STUDENT_2)

        # ---------------------------------------------------------------- #
        # 3. tenants on the student side                                   #
        # ---------------------------------------------------------------- #
        days = cfg["days"]
        day = days[-1]
        s, mine = stu.call("/api/tickets")
        before = {t["id"] for t in mine["tickets"]}

        # Student A lives in shaw A-301. The body below asks for a room in a
        # different college entirely — and the server has to ignore it. This is
        # the property the app was missing: the address rides on the account,
        # so a report can only ever come from where its author lives.
        s, t1 = stu.call("/api/tickets", {
            "lang": "zh", "college": "muse", "block": "B 座", "dorm": "Z-901",
            "repair_type": "chair", "priority": "normal", "date": day,
            "slots": [[2, 6]], "description": "边界测试：A 同学的椅子"})
        check("student A creates a ticket", s == 200,
              t1.get("code") or t1.get("ticket", {}).get("id"))
        a_id = t1["ticket"]["id"]
        got = t1["ticket"]
        check("the address in the body is ignored — the ticket uses A's own room",
              got["college"] == "shaw" and got["dorm"] == "A-301",
              "%s / %s / %s" % (got.get("college"), got.get("block"), got.get("dorm")))
        check("an ordinary report is not flagged as a public area",
              got.get("public") is False, got.get("public"))

        s, t2 = stu2.call("/api/tickets", {
            "lang": "zh", "college": "muse", "dorm": "Z-902", "repair_type": "chair",
            "priority": "normal", "date": day, "slots": [[2, 6]],
            "description": "边界测试：B 同学的椅子"})
        b_id = t2["ticket"]["id"]
        check("a second student's ticket carries THEIR room instead",
              t2["ticket"]["college"] == "diligentia" and t2["ticket"]["dorm"] == "F-303",
              "%s %s" % (t2["ticket"].get("college"), t2["ticket"].get("dorm")))

        # an AC ticket is guaranteed to go to the only certified technician,
        # which is what makes the cross-worker check deterministic
        s, t3 = stu.call("/api/tickets", {
            "lang": "zh", "repair_type": "ac",
            "priority": "urgent", "date": day, "slots": [[1, 8]],
            "description": "边界测试：空调（须持证）"})
        ac_id = t3["ticket"]["id"]

        # ...and a plumbing ticket is guaranteed to go to C03 (the only
        # technician whose trade covers plumbing), which gives us a third queue
        # to test against.
        s, t4 = stu.call("/api/tickets", {
            "lang": "zh", "repair_type": "plumbing",
            "priority": "normal", "date": day, "slots": [[1, 8]],
            "description": "边界测试：下水道（水暖工种）"})
        plumb_id = t4["ticket"]["id"]

        s, mine = stu.call("/api/tickets")
        ids = {t["id"] for t in mine["tickets"]}
        check("student A sees their own new tickets", a_id in ids and ac_id in ids, sorted(ids))
        check("student A does NOT see student B's ticket", b_id not in ids)

        # Not one row of A's list is anywhere else. Before this change a single
        # account could legitimately hold tickets in nine colleges.
        strays = sorted({(t["college"], t["dorm"]) for t in mine["tickets"]
                         if (t["college"], t["dorm"]) != ("shaw", "A-301")})
        check("every row in student A's list sits in A's own room", not strays, strays)

        s, theirs = stu2.call("/api/tickets")
        ids2 = {t["id"] for t in theirs["tickets"]}
        check("student B sees only their own ticket", b_id in ids2 and a_id not in ids2)

        # ---------------------------------------------------------------- #
        # 3b. the one legitimate exception: a shared area                  #
        # ---------------------------------------------------------------- #
        s, pub = stu.call("/api/tickets", {
            "lang": "zh", "public_area": True, "college": "muse", "dorm": "洗衣房",
            "repair_type": "other", "priority": "normal", "date": day,
            "slots": [[2, 6]], "description": "边界测试：公共洗衣房"})
        p = pub.get("ticket") or {}
        check("a shared area may be named — and is flagged as public",
              s == 200 and p.get("college") == "muse" and p.get("dorm") == "洗衣房"
              and p.get("public") is True,
              "%s %s public=%s" % (p.get("college"), p.get("dorm"), p.get("public")))

        s, d = stu.call("/api/tickets", {
            "lang": "zh", "public_area": True, "college": "muse",
            "date": day, "slots": [[2, 6]]})
        check("a shared-area report still has to say where -> 400",
              s == 400 and d.get("code") == "bad_dorm", "%d %s" % (s, d.get("code")))
        s, d = stu.call("/api/tickets", {
            "lang": "zh", "public_area": True, "college": "nosuch", "dorm": "走廊",
            "date": day, "slots": [[2, 6]]})
        check("a shared-area report still has to name a real college -> 400",
              s == 400 and d.get("code") == "bad_college", "%d %s" % (s, d.get("code")))
        s, d = stu.call("/api/tickets", {
            "lang": "zh", "dorm": "H-702", "college": "duan", "block": "",
            "repair_type": "other", "priority": "normal", "date": day,
            "slots": [[2, 6]], "description": "边界测试：不带开关的越权地址"})
        check("without the flag the body's address is still ignored",
              s == 200 and d["ticket"]["dorm"] == "A-301",
              "%s %s" % (d.get("ticket", {}).get("college"), d.get("ticket", {}).get("dorm")))

        # ---------------------------------------------------------------- #
        # 4. IDOR: touching somebody else's row                            #
        # ---------------------------------------------------------------- #
        s, d = stu2.call("/api/tickets/status", {"id": a_id, "status": "pending"})
        check("student B cannot change student A's ticket -> 403",
              s == 403 and d.get("code") == "forbidden", "%d %s" % (s, d.get("code")))
        s, d = stu2.call("/api/tickets/delete", {"id": a_id})
        check("student B cannot delete student A's ticket -> 403",
              s == 403, "%d %s" % (s, d.get("code")))
        s, d = stu2.call("/api/tickets/feedback", {"id": a_id, "rating": 1})
        check("student B cannot rate student A's ticket -> 403", s == 403, "%d %s" % (s, d.get("code")))
        s, d = stu2.call("/api/tickets/delete", {"id": "T00000000"})
        check("deleting a ticket that does not exist -> 404", s == 404, "%d %s" % (s, d.get("code")))

        # ---------------------------------------------------------------- #
        # 5. the technician side sees only its own jobs                    #
        # ---------------------------------------------------------------- #
        s, mine = tech.call("/api/tickets")
        rows = mine["tickets"]
        own = {t["id"] for t in rows}
        wandered = [t["id"] for t in rows
                    if (t.get("assigned_worker") or {}).get("worker_id") != tech.user["worker_id"]]
        check("technician A01 sees only jobs assigned to A01", not wandered, wandered)

        s, other = tech2.call("/api/tickets")
        other_ids = {t["id"] for t in other["tickets"]}
        check("the two technicians' queues are disjoint", not (own & other_ids), sorted(own & other_ids))
        check("a student's AC ticket is not in A01's queue (A01 is not certified)",
              ac_id in other_ids and ac_id not in own)

        s, d = tech.call("/api/tickets/status", {"id": ac_id, "status": "done"})
        check("A01 cannot complete B02's job -> 403",
              s == 403 and d.get("code") == "forbidden", "%d %s" % (s, d.get("code")))

        s, c03 = tech3.call("/api/tickets")
        c03_ids = {t["id"] for t in c03["tickets"]}
        check("A01's and C03's queues are disjoint", not (own & c03_ids), sorted(own & c03_ids))
        check("B02's and C03's queues are disjoint", not (other_ids & c03_ids),
              sorted(other_ids & c03_ids))
        check("the plumbing ticket went to C03, the plumbing trade",
              plumb_id in c03_ids and plumb_id not in own,
              "C03=%s A01 has it=%s" % (plumb_id in c03_ids, plumb_id in own))
        s, d = tech.call("/api/tickets/status", {"id": plumb_id, "status": "accepted"})
        check("A01 cannot accept a job on C03's route -> 403",
              s == 403 and d.get("code") == "forbidden", "%d %s" % (s, d.get("code")))
        s, d = tech.call("/api/tickets/status", {"id": plumb_id, "status": "done"})
        check("A01 cannot complete a job on C03's route -> 403", s == 403, s)

        # ---------------------------------------------------------------- #
        # 6. the `worker` parameter is not an identity                     #
        # ---------------------------------------------------------------- #
        s, plan = tech.call("/api/schedule/optimize",
                            {"date": day, "worker": "W-C03", "lang": "zh"})
        check("technician cannot plan somebody else's day via the body",
              s == 200 and plan["worker"]["worker_id"] == tech.user["worker_id"],
              (plan.get("worker") or {}).get("worker_id"))
        s, plan = tech.call("/api/plan?date=%s&worker=W-C03" % day)
        check("technician cannot read somebody else's day via the query string",
              s == 200 and plan["worker"]["worker_id"] == tech.user["worker_id"],
              (plan.get("worker") or {}).get("worker_id"))

        # ---------------------------------------------------------------- #
        # 7. role-only endpoints                                           #
        # ---------------------------------------------------------------- #
        for label, client, path, body in [
            ("technician cannot use the AI intake", tech, "/api/intake/parse", {"text": "灯坏了"}),
            ("technician cannot file a repair", tech, "/api/tickets",
             {"college": "shaw", "dorm": "A-1", "date": day, "slots": [[1, 2]]}),
            ("student cannot run the scheduler", stu, "/api/schedule/optimize", {"date": day}),
            ("student cannot read the technician plan", stu, "/api/plan?date=%s" % day, None),
            ("student cannot ask for a dispatch briefing", stu, "/api/dispatch/advice",
             {"date": day, "mode": "dispatch"}),
            # The off-route panel is a technician's judgement call; the rebooking
            # answer belongs to the student. Neither may cross over.
            ("student cannot take a job en route", stu, "/api/tickets/offroute",
             {"id": a_id, "action": "take"}),
            ("student cannot suggest a rebooking", stu, "/api/tickets/offroute",
             {"id": a_id, "action": "reschedule", "suggested_date": day}),
            ("technician cannot answer a rebooking offer", tech, "/api/tickets/reschedule",
             {"id": a_id, "accept": True}),
        ]:
            s, d = client.call(path, body)
            check("%s -> 403" % label, s == 403 and d.get("code") == "forbidden",
                  "%d %s" % (s, d.get("code")))

        # ---------------------------------------------------------------- #
        # 8. enrolment needs a session, and cross-site POSTs are refused    #
        # ---------------------------------------------------------------- #
        s, d = anon.call("/api/setup", {"key": "sk-" + "x" * 30})
        check("anonymous cannot enrol a model key -> 401", s == 401, "%d %s" % (s, d.get("code")))

        s, d = stu.call("/api/tickets/status", {"id": a_id, "status": "pending"},
                        headers={"Origin": "https://evil.example"})
        check("cross-origin POST is refused -> 403", s == 403 and d.get("code") == "bad_origin",
              "%d %s" % (s, d.get("code")))

        s, d = stu.call("/api/tickets/status", {"id": a_id, "status": "pending"},
                        headers={"Origin": "https://dormcare.example"})
        check("configured public origin is accepted behind a proxy -> 200",
              s == 200 and d.get("ok"), "%d %s" % (s, d.get("code")))

        s, d = stu.call("/api/tickets/status", {"id": a_id, "status": "pending"},
                        headers={"Origin": "https://proxy.dormcare.example",
                                 "X-Forwarded-Host": "proxy.dormcare.example",
                                 "X-Forwarded-Proto": "https"})
        check("trusted forwarded host and protocol are accepted -> 200",
              s == 200 and d.get("ok"), "%d %s" % (s, d.get("code")))

        proxy_login = Client(BASE)
        s, d = proxy_login.call(
            "/api/auth/login",
            {"username": DEMO_STUDENT[0], "password": DEMO_STUDENT[1]},
            headers={"Origin": "https://proxy.dormcare.example",
                     "X-Forwarded-Host": "proxy.dormcare.example",
                     "X-Forwarded-Proto": "https"})
        check("HTTPS proxy login marks the session cookie Secure",
              s == 200 and "Secure" in proxy_login.last_set_cookie,
              proxy_login.last_set_cookie)

        # ---------------------------------------------------------------- #
        # 9. sign-up rules                                                 #
        # ---------------------------------------------------------------- #
        # A fresh number each run: the store is a real file that persists, and
        # "already taken" is a check we want to make on purpose, not by accident.
        new_number = "9" + str(int(time.time()))[-7:]
        fresh = Client(BASE)
        s, d = fresh.register(new_number, "新同学", "dorm2026", "muse", "", "B-808",
                              phone="138-0000 0099")
        check("a new student can self-register", s == 200 and d["user"]["role"] == "student", d)
        check("registration normalizes and returns the caller's own phone",
              d.get("user", {}).get("phone") == "13800000099", d.get("user"))
        check("self-registration issues a session (auto sign-in)", bool(fresh.cookie))
        s, d = fresh.call("/api/tickets")
        check("the new student starts with an empty list", s == 200 and d["tickets"] == [], d.get("tickets"))
        s, d = fresh.call("/api/auth/me")
        u = d.get("user") or {}
        check("/api/auth/me exposes the room the account signed up with",
              u.get("college") == "muse" and u.get("dorm") == "B-808",
              "%s / %s" % (u.get("college"), u.get("dorm")))

        dup = Client(BASE)
        s, d = dup.register(new_number, "重名", "dorm2026", "muse", "", "B-808")
        check("duplicate account -> 409", s == 409 and d.get("code") == "auth_username_taken")
        s, d = dup.register("bad name!", "谁", "dorm2026")
        check("illegal characters in the account -> 400", s == 400 and d["code"] == "auth_bad_username")
        s, d = dup.register("20247777", "谁", "123")
        check("short password -> 400", s == 400 and d["code"] == "auth_weak_password")
        s, d = dup.register("20247777", "", "dorm2026")
        check("missing name -> 400", s == 400 and d["code"] == "auth_missing_name")
        s, d = dup.register("20247777", "谁", "dorm2026", "muse", "", "B-808",
                            phone="")
        check("missing registration phone -> 400",
              s == 400 and d.get("code") == "auth_missing_phone", d.get("code"))
        s, d = dup.register("20247777", "谁", "dorm2026", "muse", "", "B-808",
                            phone="<script>13800000000")
        check("invalid/injected registration phone -> 400",
              s == 400 and d.get("code") == "auth_bad_phone", d.get("code"))

        # The room is mandatory: it is the address every later ticket carries,
        # so an account without one could never file a repair.
        s, d = dup.register("20247777", "谁", "dorm2026", "", "", "A-1")
        check("no college -> 400", s == 400 and d["code"] == "auth_missing_college",
              "%d %s" % (s, d.get("code")))
        s, d = dup.register("20247777", "谁", "dorm2026", "muse", "", "")
        check("no room number -> 400", s == 400 and d["code"] == "auth_missing_dorm",
              "%d %s" % (s, d.get("code")))
        s, d = dup.register("20247777", "谁", "dorm2026", "shaw", "", "A-301")
        check("a college with real towers requires one -> 400",
              s == 400 and d["code"] == "auth_need_block", "%d %s" % (s, d.get("code")))
        s, d = dup.register("20247777", "谁", "dorm2026", "shaw", "C 座", "A-301")
        check("another college's tower is refused -> 400",
              s == 400 and d["code"] == "auth_need_block", "%d %s" % (s, d.get("code")))
        s, d = dup.register("20247777", "谁", "dorm2026", "muse", "", "B-808")
        check("a college without towers needs none -> 200", s == 200, d.get("code"))

        # ---------------------------------------------------------------- #
        # 10. logout revokes the session server-side                       #
        # ---------------------------------------------------------------- #
        victim = Client(BASE, *DEMO_STUDENT)
        stolen = victim.cookie
        victim.logout()
        replay = Client(BASE)
        replay.cookie = stolen
        s, d = replay.call("/api/tickets")
        check("a revoked token is refused (logout is server-side)",
              s == 401, "%d %s" % (s, d.get("code")))

        # ---------------------------------------------------------------- #
        # 11. brute-force budget, keyed per (caller, account)              #
        # ---------------------------------------------------------------- #
        # Deliberately aimed at an account nobody else uses, so the lockout
        # cannot poison the rest of the run.
        target = Client(BASE)
        codes = []
        for _ in range(12):
            codes.append(target.call("/api/auth/login",
                                     {"username": "brute-target", "password": "nope"})[0])
        check("repeated failures eventually rate-limit -> 429",
              codes[-1] == 429 and codes[0] == 401, codes)
        s, d = stu.call("/api/auth/login", {"username": DEMO_STUDENT[0], "password": DEMO_STUDENT[1]})
        check("the lockout is per account, so other students are unaffected", s == 200, s)

        print("\n=== %d checks passed, %d failed ===" % (len(PASSES), len(FAILS)))
        for f in FAILS:
            print("  !! " + f)
        return 0 if not FAILS else 2
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=8)
        except Exception:
            proc.kill()
        tail = server_output(log_path, 4000)
        if "Traceback" in tail:
            print("--- server log ---")
            print(tail)


if __name__ == "__main__":
    sys.exit(main())
