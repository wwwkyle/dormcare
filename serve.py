"""Start the DormCare server and keep it running for the live preview.

The sandbox blocks detached process launch from the shell, so the reliable way
is to start the server as a child of this script and simply keep this script
alive. It seeds the demo dataset (signing in with the seeded accounts, because
every endpoint now needs a session), prints one technician's plan, then blocks.

Stop it by ending this task.
"""
import os
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from testclient import (DEMO_ADMIN, DEMO_STUDENT, DEMO_WORKERS,  # noqa: E402
                        Client)

PORT = int(os.environ.get("PORT", "3000"))
BASE = "http://127.0.0.1:%d" % PORT

key = ""
for c in (HERE / "api_key.env", HERE.parent / "api_key.env", HERE.parent.parent / "api_key.env"):
    if c.is_file():
        key = c.read_text(encoding="utf-8").strip()
        break


def main():
    env = dict(os.environ)
    env["PORT"] = str(PORT)
    if key:
        env["OPENROUTER_API_KEY"] = key
    log = open(HERE / "server.log", "w", encoding="utf-8")
    proc = subprocess.Popen([sys.executable, "app.py"], cwd=str(HERE), env=env,
                            stdout=log, stderr=subprocess.STDOUT, text=True)
    print("DormCare starting on %s (key %s, pid %d)"
          % (BASE, "set" if key else "MISSING", proc.pid), flush=True)

    probe = Client(BASE)
    for _ in range(60):
        time.sleep(0.5)
        try:
            if probe.call("/api/health")[0] == 200:
                break
        except Exception:
            pass
    else:
        print("server failed to start", flush=True)
        return 1

    # ---- seed the demo dataset ------------------------------------------- #
    # One account per room.
    #
    # The address of a ticket is the room on the account that filed it, so ten
    # tickets spread across nine colleges necessarily mean ten students. Filling
    # the technician's day by filing all ten from a single account is precisely
    # the bug this design removes — and it also makes the dataset honest: every
    # student's record list sits in exactly one building.
    worker = Client(BASE, *DEMO_WORKERS[1])

    cfg = worker.call("/api/config")[1]
    days = cfg.get("days", [])
    day = days[0]

    demo = [
        # (account, college, block, dorm, repair, priority, slots, desc, contact)
        ("2024001", "shaw", "东座 1 栋", "A-301", "ac", "urgent", [[2, 8]],
         "空调漏水而且不制冷，晚上噪音很大", "138-0000-1234"),
        ("2024003", "muse", "", "B-102", "chair", "high", [[4, 10]],
         "椅子气压杆坏了，坐下去会往下滑", "139-0000-5678"),
        ("2024010", "muse", "", "B-205", "light", "normal", [[6, 16]],
         "书桌上方的灯管不亮了，换了灯管也没用", "137-0000-9012"),
        ("2024004", "harmonia", "宿舍 2 栋", "C-410", "plumbing", "urgent", [[1, 6]],
         "洗手台下水道堵塞，水漫出来了", "136-0000-3456"),
        ("2024005", "ling", "C 座", "D-201", "bed", "normal", [[3, 12]],
         "上铺床板有条裂缝，翻身会响", "135-0000-7890"),
        ("2024009", "shaw", "西座 2 栋", "A-415", "desk", "low", [[5, 14]],
         "书桌抽屉滑轨卡住了，拉不开", "134-0000-2345"),
        ("2024006", "minerva", "南区", "E-508", "network", "high", [[7, 15]],
         "网口不通，电脑显示网线未连接", "133-0000-6789"),
        ("2024002", "diligentia", "A 座", "F-303", "wardrobe", "normal", [[2, 9]],
         "衣柜门合页松了，门掉下来了", "132-0000-1122"),
        ("2024007", "eighth", "北区", "G-101", "door", "high", [[0, 5]],
         "房间门锁坏了一直锁不上，人不在时房间不安全", "131-0000-4455"),
        ("2024008", "duan", "", "H-702", "floor", "low", [[9, 16]],
         "地板有一块翘起来了，走路会绊到", "130-0000-7788"),
        # 11th job, deliberately impossible: a 40-minute electrical repair in a
        # single 30-minute window. No planner can place it, which is exactly the
        # case the technician has to answer by hand — take it en route, or ask
        # the student to rebook. It is here so the demo shows something real on
        # both sides of that loop instead of an empty panel.
        ("2024006", "minerva", "南区", "E-508", "power", "high", [[4, 4]],
         "插座一插大功率电器就跳闸，只有半小时有空，抱歉", "133-0000-6789"),
    ]

    sessions = {}
    n = 0
    def client_for(account):
        """The account's session, cleared of anything it filed on an earlier run.

        The sweep happens ONCE per account, the first time it is used — not per
        ticket. A student may legitimately file more than once (the demo has one
        who does), and clearing inside the ticket loop would delete the first of
        their two tickets the moment the second was filed.
        """
        if account not in sessions:
            cli = Client(BASE, account, DEMO_STUDENT[1])
            for t in cli.call("/api/tickets")[1].get("tickets", []):
                cli.call("/api/tickets/delete", {"id": t["id"]})
            sessions[account] = cli
        return sessions[account]

    for account, college, block, dorm, rtype, pri, slots, desc, contact in demo:
        cli = client_for(account)
        # Note what is NOT sent: college, block and dorm. The server reads the
        # address off the account, so the request cannot place the job anywhere
        # else. If a seeded room is wrong, the ticket lands in the wrong place
        # and the check below says so.
        r = cli.call("/api/tickets", {
            "lang": "zh", "repair_type": rtype, "priority": pri, "date": day,
            "slots": slots, "description": desc, "contact": contact})[1]
        got = (r.get("ticket") or {})
        if not r.get("ok"):
            print("  ! %s failed to file: %r" % (account, r), flush=True)
        elif got.get("dorm") != dorm:
            print("  ! %s landed on %s, expected %s — check SEED_USERS"
                  % (account, got.get("dorm"), dorm), flush=True)
        else:
            n += 1
    print("seeded %d demo tickets on %s, one account per room, %d accounts"
          % (n, day, len(sessions)), flush=True)

    plan = worker.call("/api/schedule/optimize", {"date": day, "lang": "zh"})[1]
    st = plan.get("stats", {})
    who = plan.get("worker", {})
    print("plan for %s (%s): %d jobs of %d assigned, %d unscheduled | work %d min, travel %d min"
          % (who.get("name"), who.get("worker_id"), len(plan.get("jobs", [])),
             st.get("assigned", 0), len(plan.get("unscheduled", [])),
             st.get("work_minutes", 0), st.get("travel_minutes", 0)), flush=True)
    for j in plan.get("jobs", []):
        print("  #%d %s %s-%s %s %s %s (%dmin)"
              % (j["seq"], j["ticket_id"], j["start"], j["end"], j["college_label"],
                 j.get("location") or j["dorm"], j["repair_label"], j["duration"]), flush=True)
    for u in plan.get("unscheduled", []):
        print("  [unscheduled] %s %s %s (%s)"
              % (u["ticket_id"], u["college_label"], u["dorm"], u["reason"]), flush=True)

    print("\nSIGN IN   -> http://127.0.0.1:%d/login.html" % PORT, flush=True)
    print("  students (password %s) — each lives in one room:" % DEMO_STUDENT[1],
          flush=True)
    seen = set()
    for account, college, block, dorm, _r, _p, _s, _d, _c in demo:
        if account in seen:
            continue
        seen.add(account)
        room = " · ".join(x for x in (college, block, dorm) if x)
        print("            %-8s %s" % (account, room), flush=True)
    print("  technician (certified, owns the HVAC/electrical trade):", flush=True)
    for u, p in DEMO_WORKERS:
        print("            %-8s %s" % (u, p), flush=True)
    print("  administrator (operator — whole board + account desk):", flush=True)
    print("            %-8s %s" % DEMO_ADMIN, flush=True)
    print("\n  Everyone lands on the SAME main menu, and each card opens a page that",
          flush=True)
    print("  does one thing only. From there:", flush=True)
    print("    student       /report.html  /records.html  /account.html", flush=True)
    print("    technician    /plan.html    /jobs.html      /account.html", flush=True)
    print("    administrator /overview.html /tickets.html  /users.html  /roster.html",
          flush=True)
    print("  Every page is gated to one role; the others are refused by the server.",
          flush=True)
    print("SERVER READY (Ctrl-C to stop)", flush=True)
    try:
        proc.wait()
    except KeyboardInterrupt:
        proc.terminate()
    return 0


if __name__ == "__main__":
    sys.exit(main())
