"""Edge-case checks: AI response quality in both languages, unscheduled reasons,
empty-day handling, and the offline model not blocking ticket submission.
"""
import sys
import re
import time
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from testclient import (DEMO_STUDENT, DEMO_STUDENT_2, DEMO_WORKERS, Client, Demo,
                        reset_store, server_output, spawn_server, store_path)

HERE = Path(__file__).resolve().parents[1]
PORT = 3113
BASE = "http://127.0.0.1:%d" % PORT

STUDENT = None
WORKER = None
DEMO = None
LOG_PATH = ""


def call(path, body=None, as_="student"):
    client = WORKER if as_ == "worker" else STUDENT
    return client.call(path, body)


def boot(key):
    """Start a server on PORT with the given key state ("" == no key).

    Each part gets its own empty store: these are two independent scenarios and
    should not inherit each other's tickets.
    """
    global LOG_PATH
    data = reset_store(store_path(HERE, "edge_cases"))
    proc, LOG_PATH = spawn_server(HERE, PORT, key, log_name="edge_cases.log",
                                  data=data)
    return proc


def sign_in():
    """(Re-)establish both sessions against the freshly started server."""
    global STUDENT, WORKER, DEMO
    STUDENT = Client(BASE, *DEMO_STUDENT)
    # B02 is the certified technician: that is who the HVAC and lighting jobs
    # are assigned to, so the plan assertions below see a non-empty day.
    WORKER = Client(BASE, *DEMO_WORKERS[1])
    DEMO = Demo(BASE)
    return STUDENT, WORKER


def wait_up():
    probe = Client(BASE)
    for _ in range(50):
        time.sleep(0.4)
        try:
            if probe.call("/api/health")[0] == 200:
                return True
        except Exception:
            pass
    return False


def main():
    key = ""
    for c in (HERE / "api_key.env", HERE.parent / "api_key.env", HERE.parent.parent / "api_key.env"):
        if c.is_file():
            key = c.read_text(encoding="utf-8").strip()
            break

    # ------------------------------------------------------------------ #
    print("=" * 76)
    print("PART A — with the model key (AI paths live)")
    print("=" * 76)
    proc = boot(key)
    try:
        assert wait_up(), "server did not start"
        sign_in()
        s, cfg = call("/api/config")
        day = cfg["days"][1]

        # --- bilingual intake on a tricky, ambiguous description ---
        cases = [
            ("洗手间地漏反味还渗水到隔壁，柜子下面都湿了", "zh"),
            ("The wardrobe door fell off its hinges and the hanging rod snapped", "en"),
            ("我的电脑连不上网，网口好像是坏的", "zh"),
            ("I keep tripping the breaker when I plug in my kettle", "en"),
        ]
        for text, lg in cases:
            s, r = call("/api/intake/parse", {"text": text, "lang": lg})
            print("  [%s] %-46s -> %-11s %-6s (%s)"
                  % (lg, text[:44], r.get("repair_type"), r.get("priority"),
                     r.get("source")))
            assert r.get("repair_type") in [t["id"] for t in cfg["repair_types"]]
            # The labour standard is not part of the student-facing contract at
            # all: it is not merely hidden in the UI, it never leaves the server.
            assert "duration_minutes" not in r, r
            # ...and instead the agent says either what it still needs to know
            # or what the student can safely try.
            assert r.get("self_help"), "no self-help came back: %r" % r
            if r.get("need_more_info"):
                assert r.get("follow_up"), "flagged need_more_info with no questions: %r" % r
            print("        self-help: %s" % " | ".join(r["self_help"]))
            if r.get("follow_up"):
                print("        asks     : %s" % " | ".join(r["follow_up"]))

        # A deliberately vague description must come back asking something —
        # that is the whole point of the follow-up path.
        s, vague = call("/api/intake/parse", {"text": "坏了", "lang": "zh"})
        print("\n  vague input -> need_more_info=%s, %d question(s), %d tip(s)"
              % (vague.get("need_more_info"), len(vague.get("follow_up") or []),
                 len(vague.get("self_help") or [])))
        print("        %s" % " | ".join(vague.get("follow_up") or []))
        assert vague.get("need_more_info") is True and vague.get("follow_up")

        # No self-help step may push a student at mains wiring, gas, a
        # disassembled appliance or a chair under the curtain rail. Mentioning
        # those subjects is fine — and useful — as long as the step tells them
        # NOT to go there, so a hit only counts when there is no prohibition
        # alongside it.
        danger = ("220", "电路", "电线", "插座", "燃气", "拆", "踩椅", "登高", "爬到",
                  "mains", "wiring", "gas", "dismantle", "climb", "stand on")
        forbid = ("不要", "别", "禁止", "勿", "等师傅", "等待师傅",
                  "never", "do not", "don't", "don't", "wait for")
        for tid in [t["id"] for t in cfg["repair_types"]]:
            s, one = call("/api/intake/parse", {"text": "宿舍的%s有问题" % tid, "lang": "zh"})
            for step in (one.get("self_help") or []):
                low = step.lower()
                bad = [d for d in danger if d in low] if not any(f in low for f in forbid) else []
                assert not bad, "%s self-help suggests %s: %s" % (tid, bad, step)
        print("  no self-help step sends a student at mains, gas, a dismantled "
              "appliance or a height (all 17 categories)")

        # --- bilingual student advice ---
        for lg in ("zh", "en"):
            s, a = call("/api/intake/advice",
                        {"text": "宿舍漏水了，地上都是水" if lg == "zh" else "There is water leaking all over my dorm floor",
                         "lang": lg,
                         "context": {"repair_type": "plumbing", "priority": "urgent",
                                     "college": "shaw", "dorm": "A-301",
                                     "window_text": "10:00-12:00"}})
            body = a.get("reply", "")
            print("\n  --- student advice [%s] %d chars ---" % (lg, len(body)))
            print("  " + body[:600].replace("\n", "\n  "))
            assert len(body) > 50
            # The labour standard is internal. Nothing the student reads may
            # quote it in minutes, whichever code path produced the text.
            leaked = re.findall(r"\d+\s*(?:分钟|min(?:ute)?s?\b)", body)
            assert not leaked, "advice leaked a duration: %s" % leaked

        # --- full plan + AI dispatch in English ---
        for c, b, d, r, p, sl in [("shaw", "东座 1 栋", "A-301", "ac", "urgent", [[2, 8]]),
                                  ("muse", "", "B-102", "chair", "high", [[4, 10]]),
                                  ("ling", "C 座", "D-201", "bed", "normal", [[9, 16]])]:
            DEMO.at(c, d).call("/api/tickets", {
                "lang": "en", "repair_type": r, "priority": p, "date": day,
                "slots": sl, "description": "test", "student_name": "Student"})
        s, plan = call("/api/schedule/optimize", {"date": day, "lang": "en"}, as_="worker")
        print("\n  plan: %d jobs / %d unscheduled" % (len(plan["jobs"]), len(plan["unscheduled"])))
        for j in plan["jobs"]:
            print("    #%d %s %s-%s %s %s" % (j["seq"], j["ticket_id"], j["start"], j["end"],
                                              j["college_label"], j["repair_label"]))

        s, dis = call("/api/dispatch/advice", {"date": day, "lang": "en",
                                               "mode": "dispatch"}, as_="worker")
        print("\n  --- AI dispatch [en] %d chars ---" % len(dis.get("reply", "")))
        print("  " + dis.get("reply", "")[:700].replace("\n", "\n  "))

        s, rat = call("/api/dispatch/advice", {"date": day, "lang": "en",
                                               "mode": "rationale"}, as_="worker")
        print("\n  --- AI rationale [en] %d chars ---" % len(rat.get("reply", "")))
        print("  " + rat.get("reply", "")[:500].replace("\n", "\n  "))

        # --- empty day must not crash ---
        far = cfg["days"][4]
        s, empty = call("/api/schedule/optimize", {"date": far, "lang": "en"}, as_="worker")
        print("\n  empty day %s -> %d jobs, first_start=%s (no crash: ok)"
              % (far, len(empty["jobs"]), empty["stats"]["first_start"]))
        assert s == 200 and empty["jobs"] == []

        s, dis_e = call("/api/dispatch/advice", {"date": far, "lang": "en"}, as_="worker")
        print("  empty-day AI briefing ok: %d chars" % len(dis_e.get("reply", "")))
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=8)
        except Exception:
            proc.kill()

    # ------------------------------------------------------------------ #
    print("\n" + "=" * 76)
    print("PART B — with NO key (must still work, rules-only, nothing blocked)")
    print("=" * 76)
    proc = boot("")
    try:
        assert wait_up(), "offline server did not start"
        sign_in()
        s, h = call("/api/health")
        print("  health: key_configured=%s enrollable=%s" % (h["key_configured"], h["enrollable"]))

        s, cfg = call("/api/config")
        day = cfg["days"][1]

        s, r = call("/api/intake/parse", {"text": "空调不制冷", "lang": "zh"})
        print("  offline intake -> %s (%s), %d tip(s) : ticket creation still available"
              % (r.get("repair_type"), r.get("source"), len(r.get("self_help") or [])))
        assert r.get("source") == "rules" and r["repair_type"] == "ac"
        # Even with no model at all, the student still gets something to act on
        # and is never handed the internal labour standard.
        assert r.get("self_help") and "duration_minutes" not in r, r

        s, r = call("/api/intake/parse", {"text": "What courses should I take?", "lang": "en"})
        print("  offline out-of-scope -> %s" % r.get("out_of_scope"))
        assert r.get("out_of_scope") is True

        # The resident of muse B-102 files it; the address rides on the account.
        s, t = DEMO.at("muse", "B-102").call("/api/tickets", {
            "lang": "zh", "repair_type": "light", "priority": "normal",
            "date": day, "slots": [[2, 6]], "description": "灯坏"})
        print("  offline ticket created -> %s at %s %s"
              % (t["ticket"]["id"], t["ticket"]["college"], t["ticket"]["dorm"]))
        assert s == 200
        made = t["ticket"]["id"]

        s, plan = call("/api/schedule/optimize", {"date": day, "lang": "zh"}, as_="worker")
        print("  offline plan -> %d jobs, work=%d travel=%d"
              % (len(plan["jobs"]), plan["stats"]["work_minutes"], plan["stats"]["travel_minutes"]))
        # Lighting is B02's trade, so this exact ticket must be on B02's day —
        # the assertion is about assignment, not about the sweep being perfect.
        assert made in [j["ticket_id"] for j in plan["jobs"]], plan["jobs"]

        s, dis = call("/api/dispatch/advice", {"date": day, "lang": "zh"}, as_="worker")
        print("  offline dispatch -> ai_enabled=%s, %d chars (deterministic fallback)"
              % (dis["ai_enabled"], len(dis.get("reply", ""))))

        s, adv = call("/api/intake/advice", {"text": "灯不亮", "lang": "zh",
                                            "context": {"repair_type": "light", "priority": "normal"}})
        print("  offline advice -> %d chars" % len(adv.get("reply", "")))
        assert len(adv.get("reply", "")) > 50

        # enrollment path — now behind a session, which is the point
        anon = Client(BASE)
        s, e = anon.call("/api/setup", {"key": "sk-not-a-real-key-000000000000"})
        print("  anonymous enrollment -> %d %s (refused)" % (s, e.get("code")))
        assert s == 401
        s, e = call("/api/setup", {"key": "sk-not-a-real-key-000000000000"})
        print("  signed-in bogus key enrollment -> %d %s" % (s, e.get("code")))

        print("\n  ALL OFFLINE PATHS WORK — a model outage never blocks a repair request.")
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=8)
        except Exception:
            proc.kill()
        tail = server_output(LOG_PATH)
        if "Traceback" in tail:
            print("--- server log ---")
            print(tail)

    # ------------------------------------------------------------------ #
    print("\n" + "=" * 76)
    print("PART C — a job that cannot fit: the technician decides, the student is told")
    print("=" * 76)
    proc = boot("")
    try:
        assert wait_up(), "server did not start"
        sign_in()
        s, cfg = call("/api/config")
        day = cfg["days"][1]
        a01 = Client(BASE, *DEMO_WORKERS[0])      # the furniture trade
        b02 = Client(BASE, *DEMO_WORKERS[1])
        home = DEMO.at("ling", "D-201")
        other_student = Client(BASE, *DEMO_STUDENT_2)

        # A bed frame takes 50 minutes; a single half-hour window can never hold
        # it. No amount of planning fixes that — which is precisely the case
        # where a human has to answer, because the engine cannot.
        s, made = home.call("/api/tickets", {
            "lang": "zh", "repair_type": "bed", "priority": "high", "date": day,
            "slots": [[2, 2]], "description": "上铺床板断了，不敢睡"})
        assert s == 200, made
        tid = made["ticket"]["id"]
        assert made["ticket"]["queue_state"] == "unplaced", made["ticket"]

        s, plan = a01.call("/api/schedule/optimize", {"date": day, "lang": "zh"})
        row = [u for u in plan["unscheduled"] if u["ticket_id"] == tid]
        assert row, "the impossible job never reached the unscheduled list"
        u = row[0]
        print("  unscheduled -> %s %s %s · %s" %
              (u["ticket_id"], u["college_label"], u["dorm"], u["repair_label"]))
        print("      why: %s | free: %s | %d min | contact: %s"
              % (u["reason"], "/".join(u["windows"]), u["duration_minutes"], u["contact"]))
        # Everything needed to make the call without ringing anybody first.
        for k in ("college_label", "dorm", "location", "student", "contact", "description",
                  "duration_minutes", "priority_label", "windows", "reason", "parts"):
            assert k in u, "unscheduled row is missing %s" % k
        assert u["reason"] == "window_too_short", u["reason"]
        assert u["windows"] and u["duration_minutes"] > 0

        # Only the technician who owns this round may answer for it.
        s, d = Client(BASE).call("/api/tickets/offroute", {"id": tid, "action": "take"})
        assert s == 401, (s, d)
        s, d = home.call("/api/tickets/offroute", {"id": tid, "action": "take"})
        assert s == 403, (s, d)
        s, d = b02.call("/api/tickets/offroute", {"id": tid, "action": "take"})
        assert s == 403, (s, d)
        s, d = a01.call("/api/tickets/offroute", {"id": tid, "action": "nonsense"})
        assert s == 400 and d.get("code") == "bad_action", (s, d)
        print("  anonymous / student / wrong technician refused; bad action -> 400")

        # ---- 顺路去修 / take it on the way past -------------------------- #
        s, d = a01.call("/api/tickets/offroute", {"id": tid, "action": "take", "lang": "zh"})
        assert s == 200 and d["ticket"]["queue_state"] == "off_route", d
        print("  A01 takes it en route -> %s" % d["ticket"]["queue_label"])
        s, seen = home.call("/api/tickets")
        mine = [t for t in seen["tickets"] if t["id"] == tid][0]
        assert mine["queue_state"] == "off_route"
        assert mine["assigned_worker"]["worker_id"] == "A01"
        print("  the student's own row says: %s · %s"
              % (mine["queue_label"], mine["assigned_worker"]["name"]))
        # It must stay OFF the optimised route: the engine never placed it, and
        # painting it onto the timeline would make the timeline a lie.
        s, plan2 = a01.call("/api/schedule/optimize", {"date": day, "lang": "zh"})
        assert tid not in [j["ticket_id"] for j in plan2["jobs"]]
        assert tid in [x["ticket_id"] for x in plan2["unscheduled"]]
        print("  still absent from the optimised timeline (honest: nobody placed it)")

        # ---- 建议改约 / suggest a new day -------------------------------- #
        s, made2 = home.call("/api/tickets", {
            "lang": "zh", "repair_type": "wardrobe", "priority": "normal",
            "date": day, "slots": [[3, 3]], "description": "柜门合页断了"})
        tid2 = made2["ticket"]["id"]
        new_day = cfg["days"][2]
        s, d = a01.call("/api/tickets/offroute", {
            "id": tid2, "action": "reschedule", "suggested_date": new_day,
            "note": "今天排满了，明天这个时段可以吗", "lang": "zh"})
        assert s == 200 and d["ticket"]["queue_state"] == "reschedule", d
        print("  A01 suggests %s — %s" % (d["ticket"]["suggested_date"],
                                          d["ticket"]["reschedule_note"]))

        s, seen = home.call("/api/tickets")
        mine2 = [t for t in seen["tickets"] if t["id"] == tid2][0]
        assert mine2["queue_state"] == "reschedule" and mine2["suggested_date"] == new_day
        print("  the student is told: %s -> %s" % (mine2["queue_label"], mine2["suggested_date"]))

        # Only the student who filed it may answer.
        s, d = a01.call("/api/tickets/reschedule", {"id": tid2, "accept": True})
        assert s == 403, (s, d)
        s, d = other_student.call("/api/tickets/reschedule", {"id": tid2, "accept": True})
        assert s == 403, (s, d)
        print("  a technician and another student are both refused -> 403")

        s, d = home.call("/api/tickets/reschedule", {"id": tid2, "accept": True, "lang": "zh"})
        assert s == 200 and d["ticket"]["date"] == new_day, d
        assert d["ticket"]["queue_state"] != "reschedule"
        print("  the student accepts -> moved to %s and back into scheduling" % d["ticket"]["date"])

        # Answering twice is not a thing.
        s, d = home.call("/api/tickets/reschedule", {"id": tid2, "accept": True})
        assert s == 409 and d.get("code") == "no_suggestion", (s, d)
        print("  answering an offer that no longer exists -> 409 no_suggestion")

        # Declining clears the offer and leaves the ticket exactly where it was.
        s, made3 = home.call("/api/tickets", {
            "lang": "zh", "repair_type": "bed", "priority": "normal", "date": day,
            "slots": [[4, 4]], "description": "床架晃动"})
        tid3 = made3["ticket"]["id"]
        a01.call("/api/tickets/offroute", {"id": tid3, "action": "reschedule",
                                           "suggested_date": cfg["days"][3]})
        s, d = home.call("/api/tickets/reschedule", {"id": tid3, "accept": False, "lang": "zh"})
        assert s == 200 and d["ticket"]["date"] == day, d
        assert d["ticket"]["queue_state"] != "reschedule"
        assert not d["ticket"]["reschedule_note"] and not d["ticket"]["suggested_date"]
        print("  the student declines -> offer cleared, date unchanged (%s)" % d["ticket"]["date"])

        # A job that IS on the route cannot be declared off-route: the panel is
        # for jobs the engine could not place, nothing else.
        s, fitted = home.call("/api/tickets", {
            "lang": "zh", "repair_type": "chair", "priority": "normal", "date": day,
            "slots": [[0, 17]], "description": "椅子脚轮坏了"})
        fit_id = fitted["ticket"]["id"]
        s, d = a01.call("/api/tickets/offroute", {"id": fit_id, "action": "take"})
        assert s == 409 and d.get("code") == "on_route", (s, d)
        print("  a job that DID fit cannot be declared off-route -> 409 on_route")

        print("\n  THE LOOP CLOSES — the engine says 'cannot fit', the technician "
              "decides, and the student is told either way.")
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=8)
        except Exception:
            proc.kill()
        tail = server_output(LOG_PATH)
        if "Traceback" in tail:
            print("--- server log ---")
            print(tail)

    print("\nALL EDGE CASES PASSED")
    return 0

if __name__ == "__main__":
    sys.exit(main())
