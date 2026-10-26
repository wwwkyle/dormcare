#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
DormCare · 管理员端测试 / Administrator console test
====================================================

The third role is the one most likely to be built as "a super-user that can do
everything". This file is the evidence that it was not: an administrator reads
the whole board and operates the accounts, and can do *nothing else* — no
filing, no accepting, no scheduling, no borrowing either console.

Written as boundaries, exactly like `test_auth.py`:

    1. anonymous callers get nothing from /api/admin/*
    2. the three roles are mutually exclusive: a student or a technician cannot
       reach an admin endpoint, and an admin cannot reach theirs
    3. an admin account cannot open, or even download, ANY page owned by a
       student or a technician — nor the other way round
    4. the whole board is readable: every ticket (with its owner) and every
       account (without its password digest)
    5. the account desk works, and its input rules are the same ones sign-up
       uses: a wrong room is correctable, a tower-less college still needs no
       tower, a duplicate account is refused, a weak password is refused
    6. resetting a password revokes every session that account still holds
    7. an issued technician account is a REAL technician: paired with a roster
       record, it signs in to the technician console and sees its own route,
       which is the whole reason technicians are not self-registered

Run it against a live server:

    python tests/test_admin.py     # starts its own server on port 3113
"""

import json
import sys
import time
from pathlib import Path

from testclient import (DEMO_STUDENT, DEMO_WORKERS, Client, reset_store,
                        server_output, spawn_server, store_path)

HERE = Path(__file__).resolve().parents[1]
PORT = 3113
BASE = "http://127.0.0.1:%d" % PORT

ADMIN = ("admin", "admin2026")

FAILS = []
PASSES = []


def check(label, ok, detail=""):
    (PASSES if ok else FAILS).append(label)
    print(("PASS  " if ok else "FAIL  ") + label + (("  -> " + str(detail)) if detail else ""))


def signin(username, password):
    """Sign in without raising — a failed sign-in is a finding, not a crash."""
    client = Client(BASE)
    status, data = client.call("/api/auth/login",
                               {"username": username, "password": password})
    if status == 200:
        client.user = data.get("user")
    return status, client


def main():
    key = ""
    for c in (HERE / "api_key.env", HERE.parent / "api_key.env"):
        if c.is_file():
            key = c.read_text(encoding="utf-8").strip()
            break

    data = reset_store(store_path(HERE, "admin"))
    proc, log_path = spawn_server(HERE, PORT, key, log_name="test_admin.log", data=data)
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
        # 0. the operator exists and is reachable                           #
        # ---------------------------------------------------------------- #
        admin = Client(BASE)
        s, d = admin.call("/api/auth/login", {"username": ADMIN[0], "password": ADMIN[1]})
        admin.user = d.get("user")
        check("administrator signs in", s == 200 and d["user"]["role"] == "admin", d.get("user", {}))
        # One front door for every role; the menu then opens this console.
        check("administrator lands on the main menu", d.get("home") == "/menu.html", d.get("home"))

        # ---------------------------------------------------------------- #
        # 1. anonymous access — the whole admin surface closes                 #
        # ---------------------------------------------------------------- #
        for path, body, method in [
            ("/api/admin/overview", None, "GET"),
            ("/api/admin/tickets", None, "GET"),
            ("/api/admin/users", None, "GET"),
            ("/api/admin/user/update", {"id": "U-1", "name": "x"}, "POST"),
            ("/api/admin/user/password", {"id": "U-1", "password": "dorm2026"}, "POST"),
            ("/api/admin/user/create", {"username": "x1", "name": "x", "password": "dorm2026",
                                        "phone": "13800000000",
                                        "college": "muse", "dorm": "B-1"}, "POST"),
            ("/api/admin/worker/create", {"worker_id": "X1", "name": "x", "password": "fix2026",
                                          "specialties": ["desk"]}, "POST"),
            ("/api/admin/ticket/create", {"owner_user_id": "U-1"}, "POST"),
            ("/api/admin/ticket/update", {"id": "T00000000"}, "POST"),
            ("/api/admin/ticket/delete", {"id": "T00000000"}, "POST"),
            ("/api/admin/audit?limit=5", None, "GET"),
            ("/api/admin/model", None, "GET"),
            ("/api/setup/reset", {}, "POST"),
        ]:
            s, d = anon.call(path, body, method)
            check("anonymous %-28s -> 401" % path, s == 401, "%d %s" % (s, d.get("code")))

        # ---------------------------------------------------------------- #
        # 2. the three roles are mutually exclusive                          #
        # ---------------------------------------------------------------- #
        stu = Client(BASE, *DEMO_STUDENT)
        tech = Client(BASE, *DEMO_WORKERS[0])
        check("student signs in", stu.user["role"] == "student")
        check("technician signs in", tech.user["role"] == "worker")

        for label, client, path, body, method in [
            ("student cannot read the board", stu, "/api/admin/overview", None, "GET"),
            ("student cannot list every account", stu, "/api/admin/users", None, "GET"),
            ("student cannot list every ticket", stu, "/api/admin/tickets", None, "GET"),
            ("student cannot correct a room", stu, "/api/admin/user/update",
             {"id": "U-1", "name": "x"}, "POST"),
            ("student cannot reset a password", stu, "/api/admin/user/password",
             {"id": "U-1", "password": "dorm2026"}, "POST"),
            ("technician cannot read the board", tech, "/api/admin/overview", None, "GET"),
            ("technician cannot issue an account", tech, "/api/admin/user/create",
             {"username": "x1", "name": "x", "password": "dorm2026",
              "phone": "13800000000", "college": "muse", "dorm": "B-1"}, "POST"),
            ("technician cannot delete a ticket", tech, "/api/admin/ticket/delete",
             {"id": "T00000000"}, "POST"),
            ("student cannot create an admin ticket", stu, "/api/admin/ticket/create",
             {"owner_user_id": "U-2024001"}, "POST"),
            ("technician cannot edit an admin ticket", tech, "/api/admin/ticket/update",
             {"id": "T00000000", "title": "x"}, "POST"),
            ("student cannot read admin audit", stu, "/api/admin/audit", None, "GET"),
            ("student cannot read the model key", stu, "/api/admin/model", None, "GET"),
            ("technician cannot reset the model key", tech, "/api/setup/reset", {}, "POST"),
        ]:
            s, d = client.call(path, body, method)
            check("%s -> 403" % label, s == 403 and d.get("code") == "forbidden",
                  "%d %s" % (s, d.get("code")))

        # ...and the operator is NOT a super-user: the other two consoles refuse
        # the admin account exactly as they refuse each other.
        for label, path, body, method in [
            ("admin cannot read the student ticket list", "/api/tickets", None, "GET"),
            ("admin cannot file a repair", "/api/tickets",
             {"repair_type": "desk", "date": "2026-10-07", "slots": [[1, 2]]}, "POST"),
            ("admin cannot use the intake agent", "/api/intake/parse", {"text": "灯坏了"}, "POST"),
            ("admin cannot run the scheduler", "/api/schedule/optimize", {"date": "2026-10-07"}, "POST"),
            ("admin cannot read a route", "/api/plan?date=2026-10-07", None, "GET"),
            ("admin cannot accept somebody's job", "/api/tickets/status",
             {"id": "T00000000", "status": "accepted"}, "POST"),
            ("admin cannot judge an off-route job", "/api/tickets/offroute",
             {"id": "T00000000", "action": "take"}, "POST"),
        ]:
            s, d = admin.call(path, body, method)
            check("%s -> 403" % label, s == 403 and d.get("code") == "forbidden",
                  "%d %s" % (s, d.get("code")))

        # ---------------------------------------------------------------- #
        # 3. page gates: three consoles, three keys                          #
        #    A console reached the wrong way bounces to the main menu, so     #
        #    the shared menu is never a way around a per-page gate.          #
        # ---------------------------------------------------------------- #
        # One page per menu card. Each is gated exactly as a single-role page
        # always was.
        admin_pages = ["/overview.html", "/tickets.html", "/users.html", "/roster.html",
                       "/model.html",
                       "/admin-overview.js", "/admin-tickets.js", "/admin-users.js",
                       "/admin-roster.js", "/admin-model.js"]
        gates = [("anonymous %s" % p, anon, p, 303, "/login.html") for p in admin_pages]
        gates += [("student %s" % p, stu, p, 303, "/menu.html") for p in admin_pages]
        gates += [("technician %s" % p, tech, p, 303, "/menu.html") for p in admin_pages]
        gates += [("administrator %s" % p, admin, p, 200, None) for p in admin_pages]
        gates += [
            ("administrator /login.html", admin, "/login.html", 200, None),
            # the pages every signed-in role may open
            ("student /menu.html", stu, "/menu.html", 200, None),
            ("technician /menu.html", tech, "/menu.html", 200, None),
            ("administrator /menu.html", admin, "/menu.html", 200, None),
            ("student /menu.js", stu, "/menu.js", 200, None),
            ("administrator /menu.js", admin, "/menu.js", 200, None),
            # account settings: the password half is valid for every role, and
            # the page hides the room half from the two that have no room
            ("student /account.html", stu, "/account.html", 200, None),
            ("technician /account.html", tech, "/account.html", 200, None),
            ("administrator /account.html", admin, "/account.html", 200, None),
            ("anonymous /account.html", anon, "/account.html", 303, "/login.html"),
            # an operator cannot borrow either of the other two consoles
            ("administrator /report.html", admin, "/report.html", 303, "/menu.html"),
            ("administrator /records.html", admin, "/records.html", 303, "/menu.html"),
            ("administrator /plan.html", admin, "/plan.html", 303, "/menu.html"),
            ("administrator /jobs.html", admin, "/jobs.html", 303, "/menu.html"),
        ]
        for label, client, path, want, want_loc in gates:
            got = client.get_text(path, follow=False)
            loc = client.last_location or ""
            good = (got == want and (want_loc is None or loc == want_loc))
            check("page gate %-28s -> %d %s" % (label, got, loc), good,
                  "wanted %d %s" % (want, want_loc))

        # ---------------------------------------------------------------- #
        # 3b. the model-key page: read the status, never the key itself       #
        # ---------------------------------------------------------------- #
        s, st = admin.call("/api/admin/model", None, "GET")
        dumped = json.dumps(st, ensure_ascii=False)
        check("administrator reads model status",
              s == 200 and "configured" in st and "key" not in st, st)
        check("model status never echoes a key",
              "api_key" not in dumped and "sk-" not in dumped, dumped)
        if st.get("locked"):
            s, d = admin.call("/api/setup/reset", {})
            check("an environment key cannot be reset from the page",
                  s == 409 and d.get("code") == "setup_disabled_env", d)
            s, d = admin.call("/api/setup", {"key": "sk-" + "x" * 20, "replace": True})
            check("an environment key cannot be replaced from the page",
                  s == 409 and d.get("code") == "setup_disabled_env", d)
        else:
            s, d = admin.call("/api/setup", {"provider": "openrouter", "key": "short"})
            check("a short key is refused before it is stored",
                  s == 400 and d.get("code") == "setup_not_a_key", d)
            s, d = admin.call("/api/setup/reset", {})
            check("reset is safe when nothing is enrolled",
                  s == 200 and d.get("code") == "setup_reset"
                  and d.get("status", {}).get("configured") is False
                  and "key" not in d.get("status", {}), d)

        # ---------------------------------------------------------------- #
        # 4. the whole board is readable                                     #
        # ---------------------------------------------------------------- #
        cfg = anon.call("/api/config")[1]
        days = cfg["days"]
        day = days[0]

        # a couple of tickets, filed by their own residents, so the board has
        # something on it and the owner field means something
        s, t1 = stu.call("/api/tickets", {
            "lang": "zh", "repair_type": "chair", "priority": "normal", "date": day,
            "slots": [[2, 6]], "description": "管理端测试：椅子"})
        check("a student files a ticket", s == 200, t1.get("code"))
        first_id = t1["ticket"]["id"]
        s, t2 = Client(BASE, "2024002", "dorm2026").call("/api/tickets", {
            "lang": "zh", "repair_type": "ac", "priority": "urgent", "date": day,
            "slots": [[1, 8]], "description": "管理端测试：空调"})
        check("a second student files a ticket", s == 200, t2.get("code"))

        s, ov = admin.call("/api/admin/overview")
        check("administrator reads the overview", s == 200 and "totals" in ov, s)
        check("overview counts every ticket",
              ov["totals"]["tickets"] == 2, ov.get("totals"))
        check("overview counts the accounts by role",
              ov["totals"]["students"] == 10 and ov["totals"]["workers"] == 3
              and ov["totals"]["admins"] == 1, ov.get("totals"))
        check("overview reports the seeded roster",
              ov["totals"]["roster"] == 3 and len(ov.get("workers", [])) == 3,
              len(ov.get("workers", [])))
        check("overview breaks the board down by status",
              ov["by_status"].get("pending") == 2, ov.get("by_status"))
        check("overview localises its labels",
              any(c.get("label") for c in ov.get("by_college", [])), ov.get("by_college"))
        check("overview carries the roster's internal extension (operator only)",
              all(w.get("phone") for w in ov["workers"]), ov["workers"][0].get("phone"))
        check("the roster reports trade coverage for the dispatcher",
              any(w.get("specialty_labels") for w in ov["workers"]),
              ov["workers"][0].get("specialty_labels"))

        s, tk = admin.call("/api/admin/tickets")
        rows = tk.get("tickets", [])
        check("administrator reads every ticket", s == 200 and len(rows) == 2, len(rows))
        with_owner = [t for t in rows if t.get("user_username")]
        check("every row names the student who filed it", len(with_owner) == 2,
              [t.get("user_username") for t in rows])
        check("a row carries the life-cycle AND queue state",
              all(t.get("status_label") and t.get("queue_label") for t in rows),
              [(t.get("status_label"), t.get("queue_label")) for t in rows])
        check("a row carries the assigned technician once the engine has one",
              all("assigned_worker" in t for t in rows))

        # the other two roles must never be handed the owner field on someone
        # else's row — a student's list is their own account and nothing else
        s, mine = stu.call("/api/tickets")
        owners = {t.get("user_username") for t in mine["tickets"]}
        check("a student's list only ever contains their own account",
              s == 200 and owners <= {DEMO_STUDENT[0]}, owners)

        s, us = admin.call("/api/admin/users")
        people = us.get("users", [])
        check("administrator lists every account", s == 200 and len(people) == 14, len(people))
        check("the account list never carries a password digest",
              "password" not in json.dumps(us) and "pbkdf2" not in json.dumps(us),
              "no digest in the payload")
        by_name = {u["username"]: u for u in people}
        check("a student row carries the room the tickets are filed against",
              by_name["2024001"]["dorm"] == "A-301"
              and by_name["2024001"]["college"] == "shaw",
              "%s %s" % (by_name["2024001"].get("college"), by_name["2024001"].get("dorm")))
        check("a student row reports how many tickets it owns",
              by_name["2024001"]["ticket_count"] == 1, by_name["2024001"]["ticket_count"])
        check("administrator can read a student's account phone",
              by_name["2024001"].get("phone") == "13800000001",
              by_name["2024001"].get("phone"))
        check("a technician row keeps the ACCOUNT id, not the roster record id",
              by_name["A01"]["id"] == "U-A01" and by_name["A01"]["worker_ref"] == "W-A01",
              "%s / %s" % (by_name["A01"].get("id"), by_name["A01"].get("worker_ref")))
        check("a technician row carries the trade the dispatcher matches on",
              "木工" in (by_name["A01"].get("trade") or ""), by_name["A01"].get("trade"))

        # ---------------------------------------------------------------- #
        # 5. the account desk                                                #
        # ---------------------------------------------------------------- #
        # --- correcting a room ------------------------------------------- #
        check("the seeded student's room is what the form says",
              by_name["2024001"]["dorm"] == "A-301")
        s, d = admin.call("/api/admin/user/update", {
            "id": by_name["2024001"]["id"], "college": "shaw",
            "block": "西座 3 栋", "dorm": "A-999"})
        check("an administrator can correct a student's room", s == 200, d.get("code"))
        check("the correction is what the account now reports",
              d["user"]["college"] == "shaw" and d["user"]["block"] == "西座 3 栋"
              and d["user"]["dorm"] == "A-999",
              "%s %s %s" % (d["user"].get("college"), d["user"].get("block"), d["user"].get("dorm")))
        # ...and the student sees it too: the address rides on the account
        s, me = stu.call("/api/auth/me")
        check("the student's own session reports the corrected room",
              me["user"]["dorm"] == "A-999", me["user"].get("dorm"))
        s, d = admin.call("/api/admin/user/update", {
            "id": by_name["2024001"]["id"], "phone": "137-0000 0066"})
        check("administrator can maintain and normalize a student's phone",
              s == 200 and d["user"].get("phone") == "13700000066", d.get("user"))
        s, me = stu.call("/api/auth/me")
        check("the student immediately sees only their own updated phone",
              me["user"].get("phone") == "13700000066", me.get("user"))
        # ...and the NEXT ticket they file lands there, which is the point
        s, tk2 = stu.call("/api/tickets", {
            "lang": "zh", "repair_type": "light", "priority": "low", "date": day,
            "slots": [[2, 6]], "description": "管理端测试：改址后"})
        check("a ticket filed after the correction uses the new room",
              tk2["ticket"]["dorm"] == "A-999", tk2["ticket"].get("dorm"))

        s, d = admin.call("/api/admin/user/update", {
            "id": by_name["2024001"]["id"], "college": "shaw", "block": "", "dorm": "A-1"})
        check("a tower-less college still demands its tower -> 400",
              s == 400 and d.get("code") == "auth_need_block", "%d %s" % (s, d.get("code")))
        s, d = admin.call("/api/admin/user/update", {
            "id": by_name["2024001"]["id"], "college": "nosuch", "dorm": "A-1"})
        check("an unknown college is refused -> 400",
              s == 400 and d.get("code") == "auth_missing_college", "%d %s" % (s, d.get("code")))
        s, d = admin.call("/api/admin/user/update",
                          {"id": by_name["2024001"]["id"], "dorm": "   "})
        check("an empty room number is refused -> 400",
              s == 400 and d.get("code") == "auth_missing_dorm", "%d %s" % (s, d.get("code")))
        s, d = admin.call("/api/admin/user/update", {"id": by_name["2024001"]["id"]})
        check("an update with nothing in it -> 400",
              s == 400 and d.get("code") == "nothing_to_update", "%d %s" % (s, d.get("code")))
        s, d = admin.call("/api/admin/user/update", {"id": "U-nobody", "name": "x"})
        check("updating an account that does not exist -> 404", s == 404, "%d %s" % (s, d.get("code")))
        s, d = admin.call("/api/admin/user/update", {"id": by_name["A01"]["id"], "name": "陈师傅（一队）"})
        check("a technician account can be renamed", s == 200 and d["user"]["name"] == "陈师傅（一队）",
              d.get("user", {}).get("name"))
        check("...and the rename does not touch the roster record id",
              d["user"]["id"] == "U-A01" and d["user"]["worker_ref"] == "W-A01",
              "%s / %s" % (d["user"].get("id"), d["user"].get("worker_ref")))

        # --- resetting a password ---------------------------------------- #
        victim = Client(BASE, "2024003", "dorm2026")
        stolen = victim.cookie
        s, d = admin.call("/api/admin/user/password",
                          {"id": by_name["2024003"]["id"], "password": "dorm2026x"})
        check("an administrator can reset a password", s == 200, d.get("code"))
        check("...and the reset reports how many sessions it revoked",
              d.get("revoked_sessions", 0) >= 1, d.get("revoked_sessions"))
        replay = Client(BASE)
        replay.cookie = stolen
        s, d = replay.call("/api/tickets")
        check("the reset revoked the old session (replay is refused)", s == 401,
              "%d %s" % (s, d.get("code")))
        s, _kit = signin("2024003", "dorm2026x")
        check("the account signs in with the new password", s == 200, s)
        s, d = admin.call("/api/admin/user/password",
                          {"id": by_name["2024003"]["id"], "password": "123"})
        check("a short password is refused -> 400",
              s == 400 and d.get("code") == "auth_weak_password", "%d %s" % (s, d.get("code")))
        s, d = admin.call("/api/admin/user/password", {"id": "U-nobody", "password": "dorm2026"})
        check("resetting an account that does not exist -> 404", s == 404, s)

        # --- issuing accounts -------------------------------------------- #
        fresh_number = "8" + str(int(time.time()))[-7:]
        s, d = admin.call("/api/admin/user/create", {
            "username": fresh_number, "name": "发放的学生", "password": "dorm2026",
            "phone": "138-0000 0077",
            "college": "muse", "block": "", "dorm": "B-808"})
        check("an administrator can issue a student account", s == 200, d.get("code"))
        check("administrator-created student phone is normalized and readable",
              d.get("user", {}).get("phone") == "13800000077", d.get("user"))
        s, issued = signin(fresh_number, "dorm2026")
        check("the issued student can sign in and starts empty",
              s == 200 and issued.user["role"] == "student"
              and issued.call("/api/tickets")[1]["tickets"] == [],
              issued.user.get("dorm") if issued.user else s)

        s, d = admin.call("/api/admin/user/create", {
            "username": fresh_number, "name": "重复", "password": "dorm2026",
            "phone": "13800000077",
            "college": "muse", "dorm": "B-808"})
        check("a duplicate account is refused -> 409",
              s == 409 and d.get("code") == "auth_username_taken", "%d %s" % (s, d.get("code")))
        s, d = admin.call("/api/admin/user/create", {
            "username": "70000001", "name": "无书院", "password": "dorm2026",
            "phone": "13800000077", "dorm": "B-1"})
        check("a student account without a college -> 400",
              s == 400 and d.get("code") == "auth_missing_college", "%d %s" % (s, d.get("code")))
        s, d = admin.call("/api/admin/user/create", {
            "username": "70000001", "name": "无楼栋", "password": "dorm2026",
            "phone": "13800000077", "college": "shaw", "dorm": "A-1"})
        check("a tower-less college still demands its tower -> 400",
              s == 400 and d.get("code") == "auth_need_block", "%d %s" % (s, d.get("code")))
        s, d = admin.call("/api/admin/user/create", {
            "username": "70000001", "name": "无宿舍", "password": "dorm2026",
            "phone": "13800000077", "college": "muse"})
        check("a student account without a room -> 400",
              s == 400 and d.get("code") == "auth_missing_dorm", "%d %s" % (s, d.get("code")))
        s, d = admin.call("/api/admin/user/create", {
            "username": "bad name!", "name": "x", "password": "dorm2026",
            "phone": "13800000077", "college": "muse", "dorm": "B-1"})
        check("an illegal account name -> 400",
              s == 400 and d.get("code") == "auth_bad_username", "%d %s" % (s, d.get("code")))

        # --- issuing a technician ---------------------------------------- #
        s, d = admin.call("/api/admin/worker/create", {
            "worker_id": "D77", "name": "发放的师傅", "trade_zh": "综合维修",
            "trade_en": "General maintenance", "phone": "0755-8800-9001",
            "certified": True, "specialties": ["desk", "light"], "password": "fix2026"})
        check("an administrator can issue a technician", s == 200, d.get("code"))
        check("the issued technician is paired with a roster record",
              (d.get("worker") or {}).get("worker_id") == "D77"
              and (d.get("user") or {}).get("worker_ref") == "W-D77",
              "%s / %s" % ((d.get("worker") or {}).get("id"),
                           (d.get("user") or {}).get("worker_ref")))
        s, hired = signin("D77", "fix2026")
        check("the issued technician signs in to the technician console",
              s == 200 and hired.user["role"] == "worker"
              and hired.user["worker_ref"] == "W-D77",
              hired.user.get("worker_ref") if hired.user else s)
        s, plan = hired.call("/api/plan?date=%s" % day)
        check("...and reads a real route (the engine knows them)", s == 200 and "jobs" in plan, s)

        s, d = admin.call("/api/admin/worker/create", {
            "worker_id": "D78", "name": "没工种", "specialties": [], "password": "fix2026"})
        check("a technician with no trade coverage -> 400",
              s == 400 and d.get("code") == "need_specialty", "%d %s" % (s, d.get("code")))
        s, d = admin.call("/api/admin/worker/create", {
            "worker_id": "D78", "name": "假类别", "specialties": ["not-a-category"],
            "password": "fix2026"})
        check("a technician whose only trade is unknown -> 400",
              s == 400 and d.get("code") == "need_specialty", "%d %s" % (s, d.get("code")))
        s, d = admin.call("/api/admin/worker/create", {
            "worker_id": DEMO_WORKERS[0][0], "name": "撞号", "specialties": ["desk"],
            "password": "fix2026"})
        check("issuing a technician on a taken staff number -> 409",
              s == 409 and d.get("code") == "auth_username_taken", "%d %s" % (s, d.get("code")))
        s, d = admin.call("/api/admin/worker/create", {
            "worker_id": "D79", "name": "x", "specialties": ["desk"], "password": "12"})
        check("a short password on an issued account -> 400",
              s == 400 and d.get("code") == "auth_weak_password", "%d %s" % (s, d.get("code")))

        # ---------------------------------------------------------------- #
        # 6. highest authority inside the ticket-management domain          #
        # ---------------------------------------------------------------- #
        admin_ticket = {
            "owner_user_id": "U-2024001",
            "title": "管理员代建：门锁",
            "repair_type": "door",
            "priority": "high",
            "date": day,
            "slots": [[2, 6]],
            "description": "门锁无法正常闭合",
            "client_request_id": "admin-create-001",
        }
        s, created = admin.call("/api/admin/ticket/create", admin_ticket)
        managed_id = (created.get("ticket") or {}).get("id")
        check("administrator creates a ticket for an existing student",
              s == 200 and created.get("created") is True and managed_id,
              "%d %s" % (s, created.get("code")))
        managed = created.get("ticket") or {}
        check("server stamps owner and defaults the student's snapshots",
              managed.get("user_id") == "U-2024001"
              and managed.get("user_username") == "2024001"
              and managed.get("college") == "shaw"
              and bool(managed.get("student_name"))
              and bool(managed.get("contact")), managed)

        s, replay = admin.call("/api/admin/ticket/create", admin_ticket)
        check("same client request id replays the same create",
              s == 200 and replay.get("idempotent_replay") is True
              and replay.get("ticket", {}).get("id") == managed_id, replay)
        conflicting = dict(admin_ticket, title="同一请求号的不同内容")
        s, d = admin.call("/api/admin/ticket/create", conflicting)
        check("reusing a request id for different data -> 409",
              s == 409 and d.get("code") == "idempotency_conflict",
              "%d %s" % (s, d.get("code")))
        s, d = admin.call("/api/admin/ticket/create",
                          dict(admin_ticket, owner_user_id="U-nobody",
                               client_request_id="admin-create-bad-owner"))
        check("admin create rejects a nonexistent owner",
              s == 400 and d.get("code") == "bad_owner", "%d %s" % (s, d.get("code")))
        s, d = admin.call("/api/admin/ticket/create",
                          dict(admin_ticket, user_id="U-2024001",
                               client_request_id="admin-create-forged"))
        check("admin create rejects arbitrary ownership fields",
              s == 400 and d.get("code") == "bad_fields", "%d %s" % (s, d.get("code")))

        s, mine = stu.call("/api/tickets")
        check("administrator-created ticket is visible to its student",
              s == 200 and managed_id in [x["id"] for x in mine.get("tickets", [])],
              [x.get("id") for x in mine.get("tickets", [])])

        version = managed.get("version")
        s, edited = admin.call("/api/admin/ticket/update", {
            "id": managed_id, "expected_version": version,
            "title": "管理员修订：门锁", "priority": "urgent",
            "student_name": "代报联系人", "contact": "13800009999",
            "college": "muse", "block": "", "dorm": "公共大厅",
            "public_area": True, "assigned_worker": "W-A01", "status": "accepted",
        })
        managed = edited.get("ticket") or {}
        check("administrator edits fields, assigns and accepts atomically",
              s == 200 and managed.get("title") == "管理员修订：门锁"
              and managed.get("priority") == "urgent"
              and managed.get("public") is True
              and managed.get("assigned_worker", {}).get("id") == "W-A01"
              and managed.get("status") == "accepted"
              and bool(managed.get("accepted_at")), managed)

        s, jobs = tech.call("/api/tickets")
        check("manual assignment makes the ticket visible to that technician",
              s == 200 and managed_id in [x["id"] for x in jobs.get("tickets", [])],
              [x.get("id") for x in jobs.get("tickets", [])])
        other_tech = Client(BASE, *DEMO_WORKERS[1])
        s, other_jobs = other_tech.call("/api/tickets")
        check("manual assignment hides the ticket from other technicians",
              managed_id not in [x["id"] for x in other_jobs.get("tickets", [])],
              [x.get("id") for x in other_jobs.get("tickets", [])])

        s, d = admin.call("/api/admin/ticket/update", {
            "id": managed_id, "expected_version": version, "title": "stale edit"})
        check("stale optimistic update -> 409",
              s == 409 and d.get("code") == "edit_conflict", "%d %s" % (s, d.get("code")))
        current_version = managed.get("version")
        s, d = admin.call("/api/admin/ticket/update", {
            "id": managed_id, "expected_version": current_version,
            "assigned_worker": "W-nobody"})
        check("assigning a nonexistent technician -> 400",
              s == 400 and d.get("code") == "bad_worker", "%d %s" % (s, d.get("code")))
        s, d = admin.call("/api/admin/ticket/update", {
            "id": managed_id, "expected_version": current_version,
            "created_at": "2000-01-01"})
        check("immutable or unknown update fields -> 400",
              s == 400 and d.get("code") == "bad_fields", "%d %s" % (s, d.get("code")))

        s, d = admin.call("/api/admin/ticket/update", {
            "id": managed_id, "expected_version": current_version, "status": "done"})
        check("admin completion without evidence is rejected by default",
              s == 400 and d.get("code") == "evidence_required", "%d %s" % (s, d.get("code")))
        s, d = admin.call("/api/admin/ticket/update", {
            "id": managed_id, "expected_version": current_version, "status": "done",
            "override_completion_evidence": True})
        check("evidence override without a reason is rejected",
              s == 400 and d.get("code") == "override_reason_required",
              "%d %s" % (s, d.get("code")))
        s, done = admin.call("/api/admin/ticket/update", {
            "id": managed_id, "expected_version": current_version, "status": "done",
            "override_completion_evidence": True,
            "override_reason": "现场旧工单补录，纸质验收单已归档"})
        managed = done.get("ticket") or {}
        check("explicit reason permits emergency completion override",
              s == 200 and managed.get("status") == "done"
              and managed.get("done_at") and managed.get("completed_at")
              and managed.get("completed_by") == "W-A01", managed)
        s, confirmed = admin.call("/api/admin/ticket/update", {
            "id": managed_id, "expected_version": managed.get("version"),
            "status": "confirmed"})
        managed = confirmed.get("ticket") or {}
        check("administrator can close any ticket",
              s == 200 and managed.get("status") == "confirmed"
              and bool(managed.get("confirmed_at")), managed)
        s, reopened = admin.call("/api/admin/ticket/update", {
            "id": managed_id, "expected_version": managed.get("version"),
            "status": "pending"})
        managed = reopened.get("ticket") or {}
        check("status rollback clears timestamps that no longer apply",
              s == 200 and managed.get("status") == "pending"
              and managed.get("accepted_at") is None
              and managed.get("done_at") is None
              and managed.get("completed_at") is None
              and managed.get("confirmed_at") is None, managed)
        s, audit = admin.call("/api/admin/audit?limit=20", None, "GET")
        audit_rows = audit.get("audit", [])
        check("completion override reason and changed field names are audited",
              s == 200 and any(
                  x.get("ticket_id") == managed_id
                  and x.get("override_reason")
                  and "status" in x.get("changed_fields", [])
                  and "description" not in x
                  for x in audit_rows), audit_rows[:3])

        # ---------------------------------------------------------------- #
        # 7. removing a ticket                                               #
        # ---------------------------------------------------------------- #
        s, d = admin.call("/api/admin/ticket/delete", {"id": managed_id})
        check("administrator deletes the managed ticket", s == 200 and d.get("ok") is True, d)
        s, audit = admin.call("/api/admin/audit?limit=10", None, "GET")
        check("ticket deletion writes a metadata-only audit event",
              s == 200 and any(x.get("action") == "delete"
                               and x.get("ticket_id") == managed_id
                               for x in audit.get("audit", [])), audit.get("audit", [])[:3])
        s, d = admin.call("/api/admin/ticket/delete", {"id": first_id})
        check("an administrator can remove a ticket", s == 200 and d.get("ok") is True, d)
        s, d = admin.call("/api/admin/tickets")
        check("...and it is gone from the board",
              first_id not in [t["id"] for t in d.get("tickets", [])], len(d.get("tickets", [])))
        s, d = admin.call("/api/admin/ticket/delete", {"id": "T00000000"})
        check("removing a ticket that does not exist -> 404", s == 404, "%d %s" % (s, d.get("code")))

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
