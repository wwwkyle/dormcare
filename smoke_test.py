"""Local smoke test for the DormCare backend.

Runs the real HTTP server in a subprocess and exercises every endpoint:
config, ticket creation, the deterministic plan, and the AI agents.
Prints a PASS/FAIL line per check so problems are obvious.

Every endpoint now needs a session, so the harness signs in as a seeded student
and a seeded technician and routes each call to the right one. The workload
spans several colleges, and a ticket's address is the account that filed it —
so it signs in as the resident of each room (see `Demo` in testclient).
"""
import base64
import json
import re
import sys
import time
from pathlib import Path

from testclient import (DEMO_ADMIN, DEMO_STUDENT, DEMO_WORKERS, PAGE_OWNER,
                        RETIRED_PAGES, Client, Demo, reset_store,
                        server_output, spawn_server, store_path)

HERE = Path(__file__).resolve().parent
KEYFILE_CANDIDATES = [
    HERE / "api_key.env",
    HERE.parent / "api_key.env",
    HERE.parent.parent / "api_key.env",
]
PORT = 3111
BASE = "http://127.0.0.1:%d" % PORT

STUDENT = None
WORKER = None
ADMIN = None
DEMO = None


def call(path, body=None, method=None, as_="student"):
    """Route a call to the student or the technician session."""
    client = WORKER if as_ == "worker" else STUDENT
    return client.call(path, body, method)


def main():
    global STUDENT, WORKER, ADMIN, DEMO
    key = ""
    for candidate in KEYFILE_CANDIDATES:
        if candidate.is_file():
            key = candidate.read_text(encoding="utf-8").strip()
            break

    data = reset_store(store_path(HERE, "smoke"))
    proc, log_path = spawn_server(HERE, PORT, key, log_name="smoke_test.log",
                                  data=data)
    try:
        # wait for boot
        ok = False
        probe = Client(BASE)
        for _ in range(40):
            time.sleep(0.5)
            try:
                s, d = probe.call("/api/health")
                if s == 200:
                    ok = True
                    break
            except Exception:
                pass
        if not ok:
            print("FAIL  server did not start")
            print(proc.poll())
            print("STDOUT:", server_output(log_path))
            return 1
        print("PASS  server started; health =", json.dumps(d, ensure_ascii=False))

        # --- sign in (everything below depends on this) ---
        STUDENT = Client(BASE, *DEMO_STUDENT)
        WORKER = Client(BASE, *DEMO_WORKERS[0])
        ADMIN = Client(BASE, *DEMO_ADMIN)
        DEMO = Demo(BASE)
        print("PASS  signed in: student %s (%s), technician %s (%s), admin %s"
              % (STUDENT.user["name"], STUDENT.user["username"],
                 WORKER.user["name"], WORKER.user["worker_id"], ADMIN.user["username"]))

        # --- config ---
        s, cfg = call("/api/config")
        n_types = len(cfg.get("repair_types", []))
        n_colleges = len(cfg.get("colleges", []))
        tr = cfg.get("travel_rules", {})
        assert s == 200 and n_types >= 15, ("config", s, n_types)
        assert tr.get("cross_college_minutes") == 15, ("cross", tr)
        assert tr.get("same_college_minutes") == 5, ("same", tr)
        print("PASS  config: %d repair types, %d colleges, travel 15/5, %d days"
              % (n_types, n_colleges, len(cfg.get("days", []))))

        # every duration is a fixed positive number
        bad = [t for t in cfg["repair_types"] if not isinstance(t["minutes"], int) or t["minutes"] <= 0]
        print(("PASS" if not bad else "FAIL") + "  all repair durations are fixed ints (%d types)" % n_types)

        days = cfg["days"]
        day = days[1] if len(days) > 1 else days[0]

        # --- technicians: same list, minus the internal extension for students ---
        s, w = call("/api/workers")
        s, w_tech = call("/api/workers", as_="worker")
        print("PASS  workers seeded: %d (student sees phone: %s, technician: %s)"
              % (len(w.get("workers", [])),
                 "phone" in (w["workers"][0] if w.get("workers") else {}),
                 "phone" in (w_tech["workers"][0] if w_tech.get("workers") else {})))

        # --- AI intake (RAG classification + follow-up / self-help) ---
        s, ai = call("/api/intake/parse", {
            "text": "我的空调漏水而且不制冷，晚上噪音很大",
            "lang": "zh"})
        print("PASS  AI intake zh ->", json.dumps(ai, ensure_ascii=False)[:300])
        assert ai["repair_type"] in [t["id"] for t in cfg["repair_types"]], ai
        # The labour standard is internal: the student-facing payload no longer
        # carries it at all.
        assert "duration_minutes" not in ai, ai
        assert ai.get("self_help"), ai
        print("PASS  intake replies with self-help, never with a duration -> %s"
              % " | ".join(ai["self_help"]))

        s, ai_en = call("/api/intake/parse", {
            "text": "The gas lift on my desk chair is broken and it keeps sinking",
            "lang": "en"})
        print("PASS  AI intake en ->", json.dumps(ai_en, ensure_ascii=False)[:300])
        assert "duration_minutes" not in ai_en and ai_en.get("self_help"), ai_en

        # A description with nothing to go on must come back as questions.
        s, thin = call("/api/intake/parse", {"text": "坏了", "lang": "zh"})
        assert thin.get("need_more_info") is True and thin.get("follow_up"), thin
        print("PASS  a thin description comes back as questions -> %s"
              % " | ".join(thin["follow_up"]))

        # --- out-of-scope guard ---
        s, oos = call("/api/intake/parse", {"text": "What is my GPA this semester?", "lang": "en"})
        print("PASS  out-of-scope guard ->", json.dumps(oos, ensure_ascii=False)[:160])
        assert oos.get("out_of_scope") is True

        # --- student advice ---
        s, adv = call("/api/intake/advice", {
            "text": "空调漏水，地上都是水",
            "lang": "zh",
            "context": {"repair_type": ai["repair_type"], "priority": ai["priority"],
                        "college": "shaw", "dorm": "A-301", "window_text": "10:00-12:00"}})
        print("PASS  student advice (%d chars)" % len(adv.get("reply", "")))
        print("      " + adv.get("reply", "")[:220].replace("\n", " | "))
        assert len(adv.get("reply", "")) > 50
        # Nothing the student reads may quote the internal labour standard.
        leaked = re.findall(r"\d+\s*(?:分钟|min(?:ute)?s?\b)", adv.get("reply", ""))
        assert not leaked, "advice leaked a duration: %s" % leaked

        # --- create tickets ---
        made = []
        #: ticket id -> the room whose resident filed it. The life-cycle check
        #: below needs it, because only the owner may accept the finished job.
        owner_of = {}
        plan_spec = [
            ("shaw", "东座 1 栋", "A-301", "ac", "urgent", [[2, 8]]),
            ("muse", "", "B-102", "chair", "high", [[4, 10]]),
            ("muse", "", "B-205", "light", "normal", [[6, 16]]),
            ("harmonia", "宿舍 2 栋", "C-410", "plumbing", "urgent", [[1, 5]]),
            ("ling", "C 座", "D-201", "bed", "normal", [[3, 12]]),
            ("shaw", "西座 2 栋", "A-415", "desk", "low", [[5, 14]]),
        ]
        for college, block, dorm, rtype, pri, sl in plan_spec:
            # One resident per room. Nothing about the address is sent: the
            # server reads it off the account, which is what stops a report
            # from arriving from a building nobody lives in.
            s, r = DEMO.at(college, dorm).call("/api/tickets", {
                "lang": "zh",
                "repair_type": rtype, "priority": pri, "date": day,
                "slots": sl, "description": "测试工单 %s" % rtype,
                "student_name": "测试同学", "contact": "13800000000",
                "ai_reason": "AI 分类：%s" % rtype, "ai_source": "model"})
            assert s == 200, (s, r)
            assert r["ticket"]["dorm"] == dorm, \
                "ticket landed on %s, expected %s" % (r["ticket"]["dorm"], dorm)
            made.append(r["ticket"]["id"])
            owner_of[r["ticket"]["id"]] = (college, dorm)
        print("PASS  created %d tickets on %s: %s" % (len(made), day, ", ".join(made)))

        # --- deterministic plan ---
        s, plan = call("/api/schedule/optimize", {"date": day, "lang": "zh"}, as_="worker")
        assert s == 200, (s, plan)
        jobs = plan["jobs"]
        print("PASS  plan: %d jobs, %d unscheduled; work=%d travel=%d"
              % (len(jobs), len(plan["unscheduled"]), plan["stats"]["work_minutes"],
                 plan["stats"]["travel_minutes"]))
        for j in jobs:
            print("      #%d %s %s-%s %s %s %smin travel_in=%d"
                  % (j["seq"], j["ticket_id"], j["start"], j["end"],
                     j["college_label"], j["dorm"], j["duration"], j["travel_in"]))

        # ---- invariant checks against the stated rules ----
        errors = []
        prev_end = None
        prev_college = None
        for j in jobs:
            # 1. fixed duration
            spec = [t for t in cfg["repair_types"] if t["id"] == j["repair_type"]][0]
            if j["duration"] != spec["minutes"]:
                errors.append("duration mismatch on %s" % j["ticket_id"])
            # 2. travel rule
            want = 15 if (prev_college is None or prev_college != j["college"]) else 5
            if j["travel_in"] != want:
                errors.append("travel_in %d != %d on %s" % (j["travel_in"], want, j["ticket_id"]))
            # 3. sequence is contiguous: prev end + travel == start (unless a
            #    window forced a later start, so start must be >= prev_end+travel)
            if prev_end is not None:
                gap = (int(j["start"][:2]) * 60 + int(j["start"][3:])) - \
                      (int(prev_end[:2]) * 60 + int(prev_end[3:]))
                if gap < j["travel_in"]:
                    errors.append("overlap/gap too small %s" % j["ticket_id"])
            prev_end, prev_college = j["end"], j["college"]
        # 4. never straddle lunch 12:00-13:30
        for j in jobs:
            s_m = int(j["start"][:2]) * 60 + int(j["start"][3:])
            e_m = int(j["end"][:2]) * 60 + int(j["end"][3:])
            if s_m < 13 * 60 + 30 and e_m > 12 * 60:
                errors.append("crosses lunch: %s %s-%s" % (j["ticket_id"], j["start"], j["end"]))
            # 5. inside the student's declared window
            tk = [t for t in call("/api/tickets", as_="worker")[1]["tickets"]
                  if t["id"] == j["ticket_id"]][0]
            wins = []
            for a, b in tk["slots"]:
                ws = 8 * 60 + a * 30
                we = 8 * 60 + (b + 1) * 30
                wins.append((ws, we))
            if not any(s_m >= ws and e_m <= we for ws, we in wins):
                errors.append("outside declared window: %s %s-%s" % (j["ticket_id"], j["start"], j["end"]))
        # 6. priority ordering (higher rank must never come after a lower one)
        ranks = [cfg["priorities"][j["priority"]]["rank"] if "rank" in cfg["priorities"][j["priority"]]
                 else {"urgent": 3, "high": 2, "normal": 1, "low": 0}[j["priority"]] for j in jobs]
        if ranks != sorted(ranks, reverse=True):
            errors.append("priority order violated: %s" % ranks)
        # 7. determinism: same input -> identical plan
        s, plan2 = call("/api/schedule/optimize", {"date": day, "lang": "zh"}, as_="worker")
        if [ (j["ticket_id"], j["start"], j["end"]) for j in plan["jobs"] ] != \
           [ (j["ticket_id"], j["start"], j["end"]) for j in plan2["jobs"] ]:
            errors.append("plan is not deterministic")

        print(("PASS" if not errors else "FAIL") +
              "  rule invariants (duration / travel / lunch / window / priority / determinism)")
        for e in errors:
            print("      !! " + e)

        # --- AI dispatch advice ---
        s, dis = call("/api/dispatch/advice", {"date": day, "lang": "zh",
                                               "mode": "dispatch"}, as_="worker")
        print("PASS  AI dispatch advice (%d chars, ai=%s)"
              % (len(dis.get("reply", "")), dis.get("ai_enabled")))
        print("      " + dis.get("reply", "")[:300].replace("\n", " | "))

        # --- AI rationale ---
        s, rat = call("/api/dispatch/advice", {"date": day, "lang": "en",
                                               "mode": "rationale"}, as_="worker")
        print("PASS  rationale en (%d chars)" % len(rat.get("reply", "")))
        print("      " + rat.get("reply", "")[:300].replace("\n", " | "))

        # --- route read-back (the session decides whose day it is) ---
        s, plan3 = call("/api/plan?date=%s" % day, as_="worker")
        print("PASS  GET /api/plan -> %d jobs" % len(plan3["jobs"]))

        # --- ticket life cycle, both halves ---
        # The technician accepts and completes; the student confirms. Neither
        # can do the other's half, and neither can touch a job that is not theirs.
        # The student half is done by whoever filed the ticket — which, now that
        # an address belongs to one account, is not always the same person.
        target = plan["jobs"][0]["ticket_id"] if plan["jobs"] else made[0]
        owner = DEMO.at(*owner_of[target])
        s, st = call("/api/tickets/status", {"id": target, "status": "accepted"}, as_="worker")
        print(("PASS" if s == 200 else "FAIL") + "  technician accepts -> %s"
              % st.get("ticket", {}).get("status"))
        proof = base64.b64encode(b"\x89PNG\r\n\x1a\n" + b"\0" * 24).decode()
        s, uploaded = call("/api/uploads", {
            "purpose": "completion",
            "images": [{"mime": "image/png", "data": proof}]}, as_="worker")
        proof_id = uploaded["uploads"][0]["id"]
        s, st = call("/api/tickets/status", {
            "id": target, "status": "done",
            "completion_image_ids": [proof_id]}, as_="worker")
        print(("PASS" if s == 200 else "FAIL") + "  technician completes -> %s"
              % st.get("ticket", {}).get("status"))
        s, fb = owner.call("/api/tickets/feedback", {"id": target, "rating": 5,
                                                    "feedback": "很好，很快就修好了"})
        print(("PASS" if s == 200 else "FAIL") + "  the resident rates -> %s★ (%s)"
              % (fb.get("ticket", {}).get("rating"), fb.get("ticket", {}).get("status")))

        # ...and a student who did not file it cannot rate it. 2024002 lives in
        # diligentia F-303 and filed nothing above, so this is never a
        # self-comparison.
        bystander = DEMO.at("diligentia", "F-303")
        s, d = bystander.call("/api/tickets/feedback", {"id": target, "rating": 1})
        print(("PASS" if s == 403 else "FAIL") +
              "  another student cannot rate it -> %d %s" % (s, d.get("code")))

        # a technician may not use a student-only endpoint, and vice versa
        s, wrong = call("/api/intake/parse", {"text": "空调不制冷"}, as_="worker")
        print(("PASS" if s == 403 else "FAIL") +
              "  student-only endpoint refuses a technician -> %d %s"
              % (s, wrong.get("code")))
        s, wrong = call("/api/schedule/optimize", {"date": day})
        print(("PASS" if s == 403 else "FAIL") +
              "  technician-only endpoint refuses a student -> %d %s"
              % (s, wrong.get("code")))

        # --- unknown route ---
        s, e404 = call("/api/nope", {})
        print(("PASS" if s == 404 else "FAIL") + "  unknown route -> %d" % s)

        # A bad college can only come from the public-area path now: for an
        # ordinary report the address is read off the account, so there is
        # nothing in the body left to be invalid.
        s, e400 = call("/api/tickets", {"public_area": True, "college": "nosuch",
                                        "dorm": "x", "date": day, "slots": [[1, 2]]})
        print(("PASS" if s == 400 and e400.get("code") == "bad_college" else "FAIL") +
              "  bad college on a public-area report -> %d %s" % (s, e400.get("code")))

        # --- the address is the account's, and cannot be dictated by the body ---
        s, lie = call("/api/tickets", {
            "college": "duan", "block": "", "dorm": "H-702",
            "repair_type": "other", "priority": "low", "date": day,
            "slots": [[5, 6]], "description": "地址越权测试"})
        claimed = (lie.get("ticket") or {})
        ok = s == 200 and claimed.get("dorm") == "A-301" and claimed.get("college") == "shaw"
        print(("PASS" if ok else "FAIL") +
              "  a body-supplied address is ignored -> landed on %s %s (account's own room)"
              % (claimed.get("college"), claimed.get("dorm")))
        call("/api/tickets/delete", {"id": claimed.get("id")})

        # --- pages: each is gated to the ONE role that owns it ---
        #
        # There is no longer a "student console" and a "technician console";
        # there are eleven pages, one per menu card, and PAGE_OWNER is the whole
        # map. A page reached the wrong way bounces to the menu, so the shared
        # menu is never a way around a per-page gate.
        anon = Client(BASE)
        clients = {"student": STUDENT, "worker": WORKER, "admin": ADMIN}
        for path, owner in sorted(PAGE_OWNER.items()):
            for role, client in sorted(clients.items()):
                got = client.get_text(path, follow=False)
                loc = client.last_location or ""
                if owner is None:
                    good = got == 200
                    want_text = "200 (shared by every signed-in role)"
                elif role == owner:
                    good = got == 200
                    want_text = "200 (owns it)"
                else:
                    good = (got == 303 and loc == "/menu.html")
                    want_text = "303 /menu.html"
                print(("PASS" if good else "FAIL") +
                      "  page gate %-9s %-22s -> %3d %-11s want %s"
                      % (role, path, got, loc, want_text))
                if not good:
                    errors.append("page gate %s %s: got %d %s, wanted %s"
                                  % (role, path, got, loc, want_text))

            got = anon.get_text(path, follow=False)
            loc = anon.last_location or ""
            good = (got == 303 and loc == "/login.html")
            print(("PASS" if good else "FAIL") +
                  "  page gate %-9s %-22s -> %3d %-11s want 303 /login.html"
                  % ("anonymous", path, got, loc))
            if not good:
                errors.append("anonymous %s: got %d %s, wanted 303 /login.html"
                              % (path, got, loc))

        # The consolidated consoles the split removed must be GONE, not aliased.
        for path in RETIRED_PAGES:
            got = STUDENT.get_text(path, follow=False)
            print(("PASS" if got == 404 else "FAIL") +
                  "  retired %-14s -> %d" % (path, got))
            if got != 404:
                errors.append("retired page %s still served -> %d" % (path, got))

        # --- static assets, as the role that is allowed to have them ---
        assets = [
            (anon, "/login.html"), (anon, "/login.js"),
            (anon, "/styles.css"), (anon, "/common.js"),
        ]
        for client, path in assets:
            body = client.call(path)
            print("PASS  static %-14s -> %d" % (path, body[0]))
            if body[0] != 200:
                errors.append("static %s -> %d" % (path, body[0]))

        # --- the third console reads the whole board ---
        s, ov = ADMIN.call("/api/admin/overview")
        ok = (s == 200 and ov["totals"]["students"] == 10
              and ov["totals"]["workers"] == 3 and ov["totals"]["admins"] == 1
              and len(ov.get("workers", [])) == 3)
        print(("PASS" if ok else "FAIL") + "  administrator overview -> %s"
              % json.dumps(ov.get("totals"), ensure_ascii=False))
        if not ok:
            errors.append("admin overview: %s" % ov.get("totals"))

        s, board = ADMIN.call("/api/admin/tickets")
        seen = {t.get("user_username") for t in board.get("tickets", [])}
        ok = s == 200 and len(board.get("tickets", [])) >= len(made) and len(seen) > 1
        print(("PASS" if ok else "FAIL") +
              "  administrator sees every ticket, owners included -> %d rows, %d owners"
              % (len(board.get("tickets", [])), len(seen)))
        if not ok:
            errors.append("admin board incomplete")

        s, people = ADMIN.call("/api/admin/users")
        ok = (s == 200 and len(people.get("users", [])) == 14
              and "pbkdf2" not in json.dumps(people))
        print(("PASS" if ok else "FAIL") +
              "  administrator lists every account, no digests -> %d accounts"
              % len(people.get("users", [])))
        if not ok:
            errors.append("admin account list")

        # ...and the operator is not a super-user
        for label, path, body in [
            ("admin cannot read the student ticket list", "/api/tickets", None),
            ("admin cannot run the scheduler", "/api/schedule/optimize", {"date": day}),
            ("admin cannot use the intake agent", "/api/intake/parse", {"text": "灯坏了"}),
        ]:
            s, d = ADMIN.call(path, body)
            good = (s == 403)
            print(("PASS" if good else "FAIL") + "  %s -> %d %s"
                  % (label, s, d.get("code")))
            if not good:
                errors.append("%s -> %d" % (label, s))

        # the retired combined script must no longer be served
        retired = anon.call("/app.js", follow=False)[0]
        print(("PASS" if retired == 404 else "FAIL") + "  retired /app.js -> %d" % retired)
        if retired != 404:
            errors.append("retired /app.js still reachable")

        print("\n=== %d tickets + %d gate checks, %d invariant errors ==="
              % (len(made), len(PAGE_OWNER) * 4 + len(RETIRED_PAGES) + len(assets), len(errors)))
        return 0 if not errors else 2
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=8)
        except Exception:
            proc.kill()
        tail = server_output(log_path)
        if "Traceback" in tail:
            print("--- server log ---")
            print(tail)


if __name__ == "__main__":
    sys.exit(main())
