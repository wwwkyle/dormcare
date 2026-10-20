#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
DormCare · 主菜单与自助账号 / Main menu and self-service account test
=====================================================================

Two features that both have one thing in common: they REMOVE a barrier
without removing a boundary.

A. The main menu. Signing in used to land each role straight on its console.
   Now everybody lands on /menu.html, which offers only that role's sections
   and links into the console at the right anchor. The evidence that this is
   navigation and NOT a new hole:

     1. all three roles land on the same menu, and each sees only its own cards
     2. the menu is served to any signed-in role, and to nobody anonymous
     3. a console reached by typing its URL still redirects a stranger away —
        the menu does not weaken the per-page gate
     4. every console carries a way back to the menu

B. Self-service. Correcting a room and changing a password used to require
   the administrator. Now the account holder can do both. The evidence that
   this is not an IDOR:

     5. there is no account id in either request: both act on the session, so
        neither can be pointed at somebody else's account
     6. the room rules are the SAME ones sign-up and the admin desk use
        (known college, tower required only where towers are recorded, room
        required, length capped)
     7. a corrected room applies to the NEXT ticket and leaves filed ones
        alone — a fault is a record of where the fault was
     8. changing a password needs the current one, refuses a weak or unchanged
        password, and revokes every OTHER session while keeping the caller's

Run it against a live server:

    python test_account.py          # starts its own server on port 3114
"""

import sys
import time
from pathlib import Path

from testclient import (DEMO_ADMIN, DEMO_STUDENT, DEMO_WORKERS, PAGE_OWNER,
                        RETIRED_PAGES, Client, reset_store, server_output,
                        spawn_server, store_path)

HERE = Path(__file__).resolve().parent
PORT = 3114
BASE = "http://127.0.0.1:%d" % PORT

ADMIN = DEMO_ADMIN

FAILS = []
PASSES = []


def check(label, ok, detail=""):
    (PASSES if ok else FAILS).append(label)
    print(("PASS  " if ok else "FAIL  ") + label + (("  -> " + str(detail)) if detail else ""))


def section(title):
    print("\n--- %s ---" % title)


def signin(username, password):
    """Sign in without raising — a failed sign-in is a finding, not a crash.

    Returns `(status, data, client)`: the raw login payload carries `home`,
    which is the thing several checks below are actually about.
    """
    client = Client(BASE)
    status, data = client.call("/api/auth/login",
                               {"username": username, "password": password})
    if status == 200:
        client.user = data.get("user")
    return status, data, client


def main():
    key = ""
    for c in (HERE / "api_key.env", HERE.parent / "api_key.env"):
        if c.is_file():
            key = c.read_text(encoding="utf-8").strip()
            break

    data = reset_store(store_path(HERE, "account"))
    proc, log_path = spawn_server(HERE, PORT, key, log_name="test_account.log", data=data)

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

        stu = Client(BASE, *DEMO_STUDENT)
        other = Client(BASE, "2024002", "dorm2026")
        tech = Client(BASE, *DEMO_WORKERS[0])
        admin = Client(BASE, *ADMIN)

        # ================================================================ #
        section("A1. everybody lands on the SAME menu")
        # ================================================================ #
        for label, client in (("student", stu), ("technician", tech),
                              ("administrator", admin)):
            check("%s signed in" % label, (client.user or {}).get("role") is not None,
                  client.user)
        # the login response carries `home`, so assert it per role
        for creds, want_role in ((DEMO_STUDENT, "student"),
                                 (DEMO_WORKERS[0], "worker"),
                                 (ADMIN, "admin")):
            s, d, _c = signin(*creds)
            check("%-14s sign-in says home = /menu.html" % want_role,
                  s == 200 and d.get("home") == "/menu.html",
                  "%d %s" % (s, d.get("home")))

        # "/" and "/menu" both bounce to the menu when signed in
        for path in ("/", "/menu", "/index"):
            status = admin.get_text(path, follow=False)
            check("signed-in %-8s -> /menu.html" % path,
                  status == 303 and admin.last_location == "/menu.html",
                  "%d %s" % (status, admin.last_location))

        # ================================================================ #
        section("A2. the menu is for signed-in people only")
        # ================================================================ #
        for path in ("/menu.html", "/menu.js", "/account.html", "/account.js"):
            status = anon.get_text(path, follow=False)
            check("anonymous %-14s -> /login.html" % path,
                  status == 303 and anon.last_location == "/login.html",
                  "%d %s" % (status, anon.last_location))

        # Every page in PAGE_OWNER with no owner is shared by all three roles:
        # the menu, and account settings (the password half is valid for
        # everyone, and the page hides the room half from the two that have no
        # room). Anonymous gets none of them.
        shared = sorted(p for p, owner in PAGE_OWNER.items() if owner is None)
        check("the shared set is the menu plus account settings",
              shared == ["/account.html", "/account.js", "/menu.html", "/menu.js"],
              shared)
        for label, client in (("student", stu), ("technician", tech),
                              ("administrator", admin)):
            for path in shared:
                status = client.get_text(path, follow=False)
                check("%-14s may open %s" % (label, path), status == 200,
                      "%d %s" % (status, client.last_location or ""))

        # ================================================================ #
        section("A3. the menu does NOT weaken the console gates")
        # ================================================================ #
        # This is the whole point of the check: a menu card is a link, and a
        # link is not permission. PAGE_OWNER says which single role may open
        # each page; everybody else must be bounced back to the menu.
        clients = {"student": stu, "worker": tech, "admin": admin}
        for path, owner in sorted(PAGE_OWNER.items()):
            if owner is None:
                continue
            for role, client in sorted(clients.items()):
                status = client.get_text(path, follow=False)
                if role == owner:
                    check("%-9s may open %-22s" % (role, path),
                          status == 200, "%d %s" % (status, client.last_location or ""))
                else:
                    check("%-9s -> %-22s -> 303 /menu.html" % (role, path),
                          status == 303 and client.last_location == "/menu.html",
                          "%d %s" % (status, client.last_location))

        # ...and the retired consolidated consoles are gone rather than aliased
        for path in RETIRED_PAGES:
            status = stu.get_text(path, follow=False)
            check("%-14s is gone -> 404" % path, status == 404,
                  "%d %s" % (status, stu.last_location or ""))

        # and the API side is unchanged too
        s, d = stu.call("/api/admin/overview")
        check("student still cannot read the admin board", s == 403, s)
        s, d = admin.call("/api/tickets")
        check("administrator still cannot use the student ticket list", s == 403, s)

        # ================================================================ #
        section("A4. every detail page carries a way back to the menu")
        # ================================================================ #
        # The control is rendered by common.js from the `backMenu` label, so the
        # assertion is on the shared script rather than on markup that could be
        # edited into irrelevance.
        s, common = other.call("/common.js", method="GET")
        text = (common or {}).get("raw", "")
        check("common.js is served to a signed-in student", s == 200, s)
        check("common.js knows the one menu path", '"/menu.html"' in text, len(text))
        check("common.js renders a back-to-menu control",
              "backMenu" in text and "MENU +" in text)

        # ================================================================ #
        section("B1. a student corrects their own room")
        # ================================================================ #
        s, me = stu.call("/api/auth/me")
        before = me["user"]
        check("the account starts in a known room",
              before["college"] == "shaw" and before["dorm"] == "A-301",
              "%s %s" % (before.get("college"), before.get("dorm")))

        s, d = stu.call("/api/account/room",
                        {"college": "muse", "block": "", "dorm": "B-909",
                         "lang": "zh"})
        check("a student may correct their own room", s == 200, d.get("code"))
        check("the server reports the room it recorded",
              d.get("user", {}).get("college") == "muse"
              and d["user"].get("dorm") == "B-909",
              "%s %s" % (d.get("user", {}).get("college"), d.get("user", {}).get("dorm")))

        # ...and it is the SESSION, not the form, that decides: a fresh read
        s, me2 = stu.call("/api/auth/me")
        check("the session reports the new room immediately",
              me2["user"]["dorm"] == "B-909", me2["user"].get("dorm"))

        # a technician's report form must not be able to reach it
        s, d = tech.call("/api/account/room",
                         {"college": "shaw", "block": "东座 1 栋", "dorm": "A-301"})
        check("a technician has no room to correct -> 403",
              s == 403 and d.get("code") == "forbidden", "%d %s" % (s, d.get("code")))
        s, d = admin.call("/api/account/room",
                          {"college": "shaw", "dorm": "A-301"})
        check("an administrator has no room to correct -> 403", s == 403, s)

        # ================================================================ #
        section("B2. there is no account id to point at somebody else")
        # ================================================================ #
        # Both endpoints take the room/password and nothing else. Naming another
        # account in the body must change only the CALLER's room — and the
        # victim is left exactly as they were.
        victim_id = other.user["id"]
        s, d = stu.call("/api/account/room",
                        {"id": victim_id, "college": "shaw",
                         "block": "东座 1 栋", "dorm": "A-301", "lang": "zh"})
        check("an `id` in the room request does NOT redirect the change",
              s == 200 and d["user"]["dorm"] == "A-301",
              "%s %s" % (d.get("user", {}).get("college"), d.get("user", {}).get("dorm")))
        s, d = other.call("/api/auth/me")
        check("the named student keeps their own room",
              d["user"]["dorm"] == "F-303" and d["user"]["college"] == "diligentia",
              "%s %s" % (d["user"].get("college"), d["user"].get("dorm")))
        # put the caller back where the next block expects
        stu.call("/api/account/room",
                 {"college": "muse", "block": "", "dorm": "B-909", "lang": "zh"})

        # ================================================================ #
        section("B3. the room rules are the sign-up rules")
        # ================================================================ #
        s, d = stu.call("/api/account/room", {"college": "nosuch", "dorm": "A-1"})
        check("an unknown college -> 400", s == 400 and d.get("code") == "auth_missing_college",
              "%d %s" % (s, d.get("code")))
        # shaw records real towers, so one is required
        s, d = stu.call("/api/account/room", {"college": "shaw", "dorm": "A-1"})
        check("a college with real towers requires one -> 400",
              s == 400 and d.get("code") == "auth_need_block", "%d %s" % (s, d.get("code")))
        # muse records only a placeholder, so none is required
        s, d = stu.call("/api/account/room", {"college": "muse", "block": "", "dorm": "B-1"})
        check("a college with no recorded towers needs none -> 200", s == 200, d.get("code"))
        s, d = stu.call("/api/account/room", {"college": "muse", "dorm": "   "})
        check("an empty room number -> 400", s == 400 and d.get("code") == "auth_missing_dorm",
              "%d %s" % (s, d.get("code")))
        long_room = "R" * 400
        s, d = stu.call("/api/account/room", {"college": "muse", "dorm": long_room})
        check("an absurdly long room number is truncated, not rejected",
              s == 200 and len(d["user"]["dorm"]) <= 30, len(d.get("user", {}).get("dorm", "")))
        s, d = anon.call("/api/account/room", {"college": "muse", "dorm": "B-1"})
        check("anonymous room correction -> 401", s == 401, s)

        # ================================================================ #
        section("B4. a corrected room applies forward, not backward")
        # ================================================================ #
        mover = Client(BASE, "2024010", "dorm2026")
        s, d = mover.call("/api/account/room",
                          {"college": "ling", "block": "C 座", "dorm": "D-777"})
        check("the moving student's room is corrected", s == 200, d.get("code"))

        # Ask the server for a real scheduling day rather than inventing one.
        s, cfg = mover.call("/api/config")
        day = (cfg.get("days") or [""])[0]
        s, made = mover.call("/api/tickets", {
            "repair_type": "chair", "priority": "normal", "date": day,
            "slots": [[1, 3]], "description": "改宿舍后的第一单"})
        check("a ticket filed after the correction uses the new room",
              s == 200 and made["ticket"]["college"] == "ling"
              and made["ticket"]["dorm"] == "D-777",
              "%s %s" % (made.get("ticket", {}).get("college"),
                         made.get("ticket", {}).get("dorm")))

        s, mine = mover.call("/api/tickets")
        old = [t for t in mine["tickets"]
               if t.get("description", "").find("改宿舍") < 0]
        check("tickets filed BEFORE the correction keep their own address",
              all(t["college"] == "muse" and t["dorm"] == "B-205" for t in old),
              sorted({(t["college"], t["dorm"]) for t in old}))

        # ================================================================ #
        section("B5. changing a password")
        # ================================================================ #
        pwd = Client(BASE, "2024009", "dorm2026")   # a second session, same account
        s, d = pwd.call("/api/account/password",
                        {"current_password": "wrong-one", "password": "dorm2027"})
        check("a wrong current password -> 400",
              s == 400 and d.get("code") == "auth_bad_current", "%d %s" % (s, d.get("code")))
        s, d = pwd.call("/api/auth/me")
        check("the account still works after a failed attempt", s == 200, s)

        s, d = pwd.call("/api/account/password",
                        {"current_password": "dorm2026", "password": "123"})
        check("a weak new password -> 400",
              s == 400 and d.get("code") == "auth_weak_password", "%d %s" % (s, d.get("code")))
        s, d = pwd.call("/api/account/password",
                        {"current_password": "dorm2026", "password": "dorm2026"})
        check("reusing the same password -> 400",
              s == 400 and d.get("code") == "auth_same_password", "%d %s" % (s, d.get("code")))

        # a second session on the same account, to prove the revocation
        twin = Client(BASE, "2024009", "dorm2026")
        s, _ = twin.call("/api/tickets")
        check("the second session is alive before the change", s == 200, s)

        s, d = pwd.call("/api/account/password",
                        {"current_password": "dorm2026", "password": "dorm2027x"})
        check("the password is changed", s == 200 and d.get("ok"), d.get("code"))
        check("at least the other session was revoked",
              (d.get("revoked_sessions") or 0) >= 1, d.get("revoked_sessions"))

        s, d = pwd.call("/api/tickets")
        check("the caller's OWN session survives the change", s == 200, s)
        s, d = twin.call("/api/tickets")
        check("the other session is signed out", s == 401, s)
        s, d, _c = signin("2024009", "dorm2026")
        check("the OLD password no longer signs in", s == 401, s)
        s, d, _c = signin("2024009", "dorm2027x")
        check("the NEW password signs in", s == 200, s)

        # Every role may change its OWN password — this is not a student perk,
        # and it is not a back door either: the current password is still
        # required, so nobody can escalate by "changing" a password they do not
        # know. Each seeded account is put back afterwards so the suite leaves
        # the store as it found it.
        for label, client, old, new in [
            ("a technician", tech, "fix2026", "fix2027x"),
            ("an administrator", admin, "admin2026", "admin2027x"),
        ]:
            s, d = client.call("/api/account/password",
                               {"current_password": "not-it", "password": new})
            check("%s still needs its current password" % label,
                  s == 400 and d.get("code") == "auth_bad_current", "%d %s" % (s, d.get("code")))
            s, d = client.call("/api/account/password",
                               {"current_password": old, "password": new})
            check("%s may change its own password" % label, s == 200, d.get("code"))
            s, _d, _c = signin(client.user["username"], new)
            check("%s signs in with the new password" % label, s == 200, s)
            # ...and back again, so the store is unchanged for whatever runs next
            s, d = client.call("/api/account/password",
                               {"current_password": new, "password": old})
            check("%s password restored for the next suite" % label, s == 200, d.get("code"))
            s, _d, _c = signin(client.user["username"], old)
            check("%s signs in with the restored password" % label, s == 200, s)

        s, d = anon.call("/api/account/password",
                         {"current_password": "x", "password": "abcdef"})
        check("anonymous password change -> 401", s == 401, s)

        # ================================================================ #
        section("B6. the administrator's desk still outranks self-service")
        # ================================================================ #
        admin2 = Client(BASE, *ADMIN)
        s, d = admin2.call("/api/admin/user/update",
                           {"id": other.user["id"], "college": "duan", "dorm": "H-111"})
        check("the admin desk still corrects any room", s == 200, d.get("code"))
        s, d = admin2.call("/api/admin/user/password",
                           {"id": other.user["id"], "password": "dorm2028"})
        check("the admin desk still resets any password", s == 200, d.get("code"))
        s, d = other.call("/api/tickets")
        check("an admin reset signs that account out everywhere", s == 401, s)

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

    print("\n=== %d checks passed, %d failed ===" % (len(PASSES), len(FAILS)))
    if FAILS:
        for label in FAILS:
            print("   FAILED: " + label)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
