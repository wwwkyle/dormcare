#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
DormCare · 宿舍智能报修与反馈系统  /  Smart Dorm Repair & Feedback System
=========================================================================

Run
---
    python app.py                       # http://localhost:3000
    python app.py --api-key sk-or-...   # or set OPENROUTER_API_KEY

Architecture
------------
    Browser ──POST /api/...──▶ app.py ──HTTPS──▶ OpenRouter /chat/completions
                                 │
                                 ├── config.py     固定知识库（工时 / 书院 / 路程）
                                 ├── scheduler.py  确定性排班引擎（纯算术）
                                 ├── ai_agents.py  四个 RAG 智能体
                                 └── store.py      JSON 存储

The division of labour is the point of the design:

    The ENGINE decides.  The MODEL explains.

Every minute in the schedule is computed by scheduler.py from the fixed values
in config.py. The language model classifies complaints and writes text, but it
never produces a number. That is why the same tickets always yield the same
plan, and why the plan is defensible.

Identity
--------
Students, technicians and administrators sign in (see auth.py) and the server
— not the browser — decides what each of them may see:

    student  creates tickets, sees ONLY their own, rates the finished job
    worker   sees ONLY the tickets the engine assigned to them
    admin    manages the whole board and accounts through dedicated admin
             endpoints, but owns no tickets and cannot borrow another role

Every endpoint checks the session before it touches data, and the three
consoles are separate pages that each redirect a visitor of the wrong role
away. The key enrolment endpoint accepts an administrator only.

Security
--------
* The OpenRouter key lives in process memory / environment only. It is never
  written into this directory (every file here becomes publicly downloadable
  once the app is published), never logged and never sent to the browser.
* Passwords are stored as PBKDF2-SHA256 digests with a per-user salt.
* Session cookies are HttpOnly + SameSite=Lax and the token is opaque, so a
  session can be revoked server-side on logout.
* Dot-files and .env-like files are refused by the static handler.
* Requests are rate limited per client IP; sign-in has its own stricter limit.
* Path traversal is blocked by matching against an explicit allow-list.

Runtime: Python standard library only. No pip install required.
"""

import hashlib
import json
import os
import posixpath
import sys
import threading
import time
from datetime import date, datetime, timedelta
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import ai_agents
import auth
import intake_agent
from ai_agents import (AIError, DEFAULT_MODEL, MSG_NOT_IN_KB, MSG_OUT_OF_SCOPE,
                       PROVIDERS, advise_dispatch, advise_student, clear_api_base,
                       parse_intake, set_api_base, summarize_plan,
                       valid_custom_base, verify_key)
from auth import (ANY_ROLE, HOME_BY_ROLE, MAX_DORM, MAX_NAME, MAX_PHONE,
                  MAX_TRADE, MAX_USERNAME, MENU_PATH, ROLE_ADMIN, ROLE_STUDENT,
                  ROLE_WORKER, SESSION_COOKIE, SESSION_DAYS, clean_name,
                  clean_username, hash_password, new_session, password_error,
                  public_user, normalize_student_phone, session_expired, username_error,
                  verify_password)
from config import (COLLEGES, COLLEGE_BY_ID, REPAIR_BY_ID, REPAIR_TYPES,
                    TRAVEL_RULES, college_blocks_text, college_label,
                    college_real_blocks, normalize_block, repair_label)
from scheduler import (PRIORITY_LEVELS, SLOT_MINUTES, assign_day, job_to_json,
                       next_workdays, repair_minutes, schedule_day,
                       schedule_index, slot_label)
from store import Store
from upload_service import (MAX_IMAGES, UploadError, public_upload, save_images)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATIC_ROOT = os.path.join(BASE_DIR, "web")
#: The store lives next to the app by default. `DORMCARE_DATA` points it
#: somewhere else, which is what lets each test suite run against its own empty
#: file instead of all of them sharing — and corrupting — one database.
DATA_FILE = (os.environ.get("DORMCARE_DATA")
             or os.path.join(BASE_DIR, "data", "store.json"))

MAX_BODY_BYTES = 36 * 1024 * 1024
UPLOAD_DIR = (os.environ.get("DORMCARE_UPLOAD_DIR")
              or os.path.join(os.path.dirname(os.path.abspath(DATA_FILE)), "uploads"))
RATE_LIMIT_MAX = 120
RATE_LIMIT_WINDOW = 60

# Public deployments commonly sit behind a reverse proxy. In that setup the
# browser sends `Origin: https://dormcare.example`, while this process may see
# an internal `Host` such as `127.0.0.1:3000`. Keep the CSRF check, but allow
# operators to declare the browser-facing origins explicitly.
#
# Example:
#   DORMCARE_PUBLIC_ORIGINS=https://dormcare.example,https://www.example.com
#   DORMCARE_TRUST_PROXY=1
#
# Forwarded headers are only trusted after the second switch is enabled:
# accepting them unconditionally would let a direct client invent its host.
PUBLIC_ORIGINS = {
    value.strip().rstrip("/")
    for value in os.environ.get("DORMCARE_PUBLIC_ORIGINS", "").split(",")
    if value.strip()
}
TRUST_PROXY = os.environ.get("DORMCARE_TRUST_PROXY", "").strip().lower() in {
    "1", "true", "yes", "on",
}
COOKIE_SECURE = os.environ.get("DORMCARE_COOKIE_SECURE", "").strip().lower() in {
    "1", "true", "yes", "on",
}

# The interface is deliberately split in three: students, technicians and
# administrators get different pages, different navigation, and no sight of each
# other's controls. common.js holds the shared chrome (i18n, markdown, health,
# setup); each page then loads exactly one role module.
#
# The fourth tuple element says who a page is for:
#   None      anybody, signed in or not — only the sign-in page and the files
#             it needs before anybody is known
#   ANY_ROLE  anybody signed in, whatever their role (the main menu)
#   a role id exactly that role
# Everything else is gated in do_GET() — an anonymous visitor is redirected to
# /login.html and a signed-in visitor of the wrong role is sent back to the menu,
# so the technician and administrator pages are not merely hidden but
# unreachable.
#
# Above the three consoles sits ONE main menu. Signing in lands everybody on
# /menu.html, and the menu then offers only the sections that role may open —
# so there is a single front door, while the consoles behind it stay exactly as
# separate as they were. Each console keeps a "back to the menu" control.
STATIC_FILES = {
    "/login.html": ("pages/login.html", "text/html; charset=utf-8", None),
    "/login.js": ("js/login.js", "application/javascript; charset=utf-8", None),
    "/styles.css": ("css/styles.css", "text/css; charset=utf-8", None),
    "/common.js": ("js/common.js", "application/javascript; charset=utf-8", None),
    "/menu.html": ("pages/menu.html", "text/html; charset=utf-8", ANY_ROLE),
    "/menu.js": ("js/menu.js", "application/javascript; charset=utf-8", ANY_ROLE),

    # ---- student: one page per menu card ---------------------------------- #
    "/report.html": ("pages/report.html", "text/html; charset=utf-8", ROLE_STUDENT),
    "/student-report.js": ("js/student-report.js", "application/javascript; charset=utf-8", ROLE_STUDENT),
    "/records.html": ("pages/records.html", "text/html; charset=utf-8", ROLE_STUDENT),
    "/student-records.js": ("js/student-records.js", "application/javascript; charset=utf-8", ROLE_STUDENT),
    # account settings: the password half is valid for every role, and the page
    # hides the room half from the two that have no room — so ANY_ROLE, not
    # ROLE_STUDENT, is the honest gate here.
    "/account.html": ("pages/account.html", "text/html; charset=utf-8", ANY_ROLE),
    "/account.js": ("js/account.js", "application/javascript; charset=utf-8", ANY_ROLE),

    # ---- technician ------------------------------------------------------- #
    "/plan.html": ("pages/plan.html", "text/html; charset=utf-8", ROLE_WORKER),
    "/worker-plan.js": ("js/worker-plan.js", "application/javascript; charset=utf-8", ROLE_WORKER),
    "/jobs.html": ("pages/jobs.html", "text/html; charset=utf-8", ROLE_WORKER),
    "/worker-jobs.js": ("js/worker-jobs.js", "application/javascript; charset=utf-8", ROLE_WORKER),

    # ---- administrator ---------------------------------------------------- #
    "/overview.html": ("pages/overview.html", "text/html; charset=utf-8", ROLE_ADMIN),
    "/admin-overview.js": ("js/admin-overview.js", "application/javascript; charset=utf-8", ROLE_ADMIN),
    "/tickets.html": ("pages/tickets.html", "text/html; charset=utf-8", ROLE_ADMIN),
    "/admin-tickets.js": ("js/admin-tickets.js", "application/javascript; charset=utf-8", ROLE_ADMIN),
    "/users.html": ("pages/users.html", "text/html; charset=utf-8", ROLE_ADMIN),
    "/admin-users.js": ("js/admin-users.js", "application/javascript; charset=utf-8", ROLE_ADMIN),
    "/roster.html": ("pages/roster.html", "text/html; charset=utf-8", ROLE_ADMIN),
    "/admin-roster.js": ("js/admin-roster.js", "application/javascript; charset=utf-8", ROLE_ADMIN),
    "/model.html": ("pages/model.html", "text/html; charset=utf-8", ROLE_ADMIN),
    "/admin-model.js": ("js/admin-model.js", "application/javascript; charset=utf-8", ROLE_ADMIN),
}

#: The three consolidated consoles that used to hold every section of a role on
#: one long page. They are gone: each menu card now opens a page that does ONE
#: thing, so the pages above are the whole interface. Kept here as documentation
#: of what was removed rather than as a live route.
RETIRED_PAGES = ("/index.html", "/worker.html", "/admin.html")

# "/" is not a file: it is a redirect to the main menu for whoever is asking.
ENTRY_PATHS = {"/", "/index", "/index.htm", "/menu", "/menu.htm"}

STORE = Store(DATA_FILE)

# 预置师傅 / seeded technicians — each carries the repair domains they cover.
SEED_WORKERS = [
    {"id": "W-A01", "name": "陈师傅", "name_en": "Master Chen", "worker_id": "A01",
     "trade_zh": "家具与木工", "trade_en": "Furniture & carpentry",
     "specialties": ["desk", "chair", "bed", "wardrobe", "furniture", "curtain", "door"],
     "certified": False, "phone": "0755-8800-1001"},
    {"id": "W-B02", "name": "李师傅", "name_en": "Master Li", "worker_id": "B02",
     "trade_zh": "制冷与强电（持证）", "trade_en": "HVAC & electrical (certified)",
     "specialties": ["ac", "ac_service", "power", "light", "appliance", "network"],
     "certified": True, "phone": "0755-8800-1002"},
    {"id": "W-C03", "name": "黄师傅", "name_en": "Master Huang", "worker_id": "C03",
     "trade_zh": "水暖与土建", "trade_en": "Plumbing & building",
     "specialties": ["plumbing", "bathroom", "floor", "door", "furniture", "light", "other"],
     "certified": False, "phone": "0755-8800-1003"},
]

# 预置账号 / demo accounts
#
# Each worker account is bound to exactly one technician record, which is the
# whole mechanism behind "a technician only ever sees their own jobs".
# Student accounts are seeded so the app is usable the moment it starts; real
# students self-register from the sign-in page.
#
# Every seeded student lives in exactly ONE room, and that room is the address
# of every ticket they file. A student cannot hold tickets in several buildings
# — that is precisely what the account-bound address is there to prevent.
DEMO_STUDENT_PASSWORD = "dorm2026"
DEMO_WORKER_PASSWORD = "fix2026"
DEMO_ADMIN_PASSWORD = "admin2026"

SEED_USERS = [
    {"role": ROLE_STUDENT, "username": "2024001", "name": "王小明", "name_en": "Wang Xiaoming",
     "college": "shaw", "block": "东座 1 栋", "dorm": "A-301",
     "phone": "13800000001", "worker_id": "", "worker_ref": ""},
    {"role": ROLE_STUDENT, "username": "2024009", "name": "赵雨桐", "name_en": "Zhao Yutong",
     "college": "shaw", "block": "西座 2 栋", "dorm": "A-415",
     "worker_id": "", "worker_ref": ""},
    {"role": ROLE_STUDENT, "username": "2024003", "name": "张若彤", "name_en": "Zhang Ruotong",
     "college": "muse", "block": "", "dorm": "B-102",
     "worker_id": "", "worker_ref": ""},
    {"role": ROLE_STUDENT, "username": "2024010", "name": "吴梓涵", "name_en": "Wu Zihan",
     "college": "muse", "block": "", "dorm": "B-205",
     "worker_id": "", "worker_ref": ""},
    {"role": ROLE_STUDENT, "username": "2024004", "name": "陈嘉禾", "name_en": "Chen Jiahe",
     "college": "harmonia", "block": "宿舍 2 栋", "dorm": "C-410",
     "worker_id": "", "worker_ref": ""},
    {"role": ROLE_STUDENT, "username": "2024005", "name": "刘一诺", "name_en": "Liu Yinuo",
     "college": "ling", "block": "C 座", "dorm": "D-201",
     "worker_id": "", "worker_ref": ""},
    {"role": ROLE_STUDENT, "username": "2024006", "name": "周明轩", "name_en": "Zhou Mingxuan",
     "college": "minerva", "block": "南区", "dorm": "E-508",
     "worker_id": "", "worker_ref": ""},
    {"role": ROLE_STUDENT, "username": "2024002", "name": "李思远", "name_en": "Li Siyuan",
     "college": "diligentia", "block": "A 座", "dorm": "F-303",
     "worker_id": "", "worker_ref": ""},
    {"role": ROLE_STUDENT, "username": "2024007", "name": "郑思远", "name_en": "Zheng Siyuan",
     "college": "eighth", "block": "北区", "dorm": "G-101",
     "worker_id": "", "worker_ref": ""},
    {"role": ROLE_STUDENT, "username": "2024008", "name": "孙皓宇", "name_en": "Sun Haoyu",
     "college": "duan", "block": "", "dorm": "H-702",
     "worker_id": "", "worker_ref": ""},
    {"role": ROLE_WORKER, "username": "A01", "name": "陈师傅", "name_en": "Master Chen",
     "college": "", "block": "", "dorm": "", "worker_id": "A01", "worker_ref": "W-A01"},
    {"role": ROLE_WORKER, "username": "B02", "name": "李师傅", "name_en": "Master Li",
     "college": "", "block": "", "dorm": "", "worker_id": "B02", "worker_ref": "W-B02"},
    {"role": ROLE_WORKER, "username": "C03", "name": "黄师傅", "name_en": "Master Huang",
     "college": "", "block": "", "dorm": "", "worker_id": "C03", "worker_ref": "W-C03"},
    # 管理员 / the operator. Owns no tickets and no route: its console is the
    # whole board plus the account desk, and it deliberately cannot open the
    # student or technician console — an admin is not a student in disguise.
    {"role": ROLE_ADMIN, "username": "admin", "name": "系统管理员", "name_en": "Administrator",
     "college": "", "block": "", "dorm": "", "worker_id": "", "worker_ref": ""},
]


# Demo board for a fresh deployment. `scripts/serve.py` used to file these
# over HTTP after a local start; a published site never runs that script, so
# an empty store showed "0 tickets". Each row belongs to the student who
# actually lives in that room. Statuses are mixed so the overview, the ticket
# board and a technician's queue all have something to show.
#
# (username, repair_type, priority, slots, description, contact,
#  status, assigned_worker, rating, feedback)
SEED_DEMO_TICKETS = [
    ("2024001", "ac", "urgent", [[2, 8]],
     "空调漏水而且不制冷，晚上噪音很大", "138-0000-1234",
     "accepted", "W-B02", 0, ""),
    ("2024003", "chair", "high", [[4, 10]],
     "椅子气压杆坏了，坐下去会往下滑", "139-0000-5678",
     "pending", "", 0, ""),
    ("2024010", "light", "normal", [[6, 16]],
     "书桌上方的灯管不亮了，换了灯管也没用", "137-0000-9012",
     "pending", "", 0, ""),
    ("2024004", "plumbing", "urgent", [[1, 6]],
     "洗手台下水道堵塞，水漫出来了", "136-0000-3456",
     "accepted", "W-C03", 0, ""),
    ("2024005", "bed", "normal", [[3, 12]],
     "上铺床板有条裂缝，翻身会响", "135-0000-7890",
     "done", "W-A01", 0, ""),
    ("2024009", "desk", "low", [[5, 14]],
     "书桌抽屉滑轨卡住了，拉不开", "134-0000-2345",
     "pending", "", 0, ""),
    ("2024006", "network", "high", [[7, 15]],
     "网口不通，电脑显示网线未连接", "133-0000-6789",
     "pending", "", 0, ""),
    ("2024002", "wardrobe", "normal", [[2, 9]],
     "衣柜门合页松了，门掉下来了", "132-0000-1122",
     "confirmed", "W-A01", 5, "门装回去了，开关正常"),
    ("2024007", "door", "high", [[0, 5]],
     "房间门锁坏了一直锁不上，人不在时房间不安全", "131-0000-4455",
     "accepted", "W-C03", 0, ""),
    ("2024008", "floor", "low", [[9, 16]],
     "地板有一块翘起来了，走路会绊到", "130-0000-7788",
     "pending", "", 0, ""),
    # Deliberately too short a window for a 40-minute electrical job, so the
    # technician console still has an "unscheduled" case to show.
    ("2024006", "power", "high", [[4, 4]],
     "插座一插大功率电器就跳闸，只有半小时有空", "133-0000-6789",
     "pending", "", 0, ""),
]


def _seed_demo_tickets_enabled():
    """Empty stores get the demo board unless this is explicitly turned off.

    Test suites set `DORMCARE_SEED_TICKETS=0` so they can count tickets from
    zero. A fresh public deploy leaves the variable unset and must seed.
    """
    return os.environ.get("DORMCARE_SEED_TICKETS", "1").strip() != "0"


def _seed_demo_tickets():
    """Write the demo board once, dated today, using each account's own room."""
    day = date.today().isoformat()
    now = datetime.now().isoformat(timespec="seconds")
    users = {u["username"]: u for u in STORE.users()}
    for (username, repair_type, priority, slots, desc, contact,
         status, worker, rating, feedback) in SEED_DEMO_TICKETS:
        user = users.get(username)
        if not user or not user.get("college") or not user.get("dorm"):
            continue
        ticket = {
            "user_id": user["id"],
            "user_username": username,
            "college": user.get("college", ""),
            "block": user.get("block", ""),
            "dorm": user.get("dorm", ""),
            "public": False,
            "repair_type": repair_type,
            "priority": priority,
            "date": day,
            "slots": [list(pair) for pair in slots],
            "description": desc,
            "student_name": user.get("name", ""),
            "contact": contact or user.get("phone", ""),
            "lang": "zh",
            "ai_reason": "",
            "ai_source": "",
            "title": "",
            "image_ids": [],
            "completion_image_ids": [],
            "agent_session_id": "",
            "status": status,
            "assigned_worker": worker,
        }
        if status in ("accepted", "done", "confirmed"):
            ticket["accepted_at"] = now
        if status in ("done", "confirmed"):
            ticket["done_at"] = now
            ticket["completed_at"] = now
            ticket["completed_by"] = worker
        if status == "confirmed":
            ticket["confirmed_at"] = now
            ticket["rating"] = rating
            ticket["feedback"] = feedback
        STORE.add_ticket(ticket)


def ensure_seed():
    """Idempotent: fill in the technicians, the demo accounts, and a demo board."""
    if not STORE.workers():
        for w in SEED_WORKERS:
            STORE.add_worker(dict(w))
    for spec in SEED_USERS:
        if STORE.user_by_username(spec["username"]):
            continue
        if spec["role"] == ROLE_WORKER:
            password = DEMO_WORKER_PASSWORD
        elif spec["role"] == ROLE_ADMIN:
            password = DEMO_ADMIN_PASSWORD
        else:
            password = DEMO_STUDENT_PASSWORD
        STORE.add_user({
            **spec,
            "id": "U-" + spec["username"],
            "password": hash_password(password),
        })
    if _seed_demo_tickets_enabled() and not STORE.tickets():
        _seed_demo_tickets()
    STORE.purge_expired_sessions()


# --------------------------------------------------------------------------- #
# 范围守则 / Deterministic out-of-scope guard
#
# The prompt forbids off-topic answers, but a hard requirement should not
# depend on model willingness. The unambiguous cases are caught here, before
# any model call — which also makes the refusal immune to prompt injection.
#
# Narrow by design: only triggers when an unmistakable off-topic subject is
# present AND no repair vocabulary at all appears, so a genuine repair question
# that happens to mention a course never gets refused.
# --------------------------------------------------------------------------- #

_OFF_TOPIC = (
    r"\bgpa\b", r"\bgrade[sd]?\b", r"\btranscript\b", r"\bexams?\b", r"\bmidterm\b",
    r"\bassignment\b", r"\bhomework\b", r"\bsyllabus\b", r"\blecture[sr]?\b",
    r"\bcourse[s]?\b", r"\bcredit[s]?\b", r"\btuition\b", r"\bscholarship\b",
    r"\bvisa\b", r"\benrol{1,2}ment\b", r"\badmission[s]?\b", r"\bprofessor\b",
    r"\bdean\b", r"\bregistrar\b", r"\bclub[s]?\b", r"\bsociety\b",
    r"绩点", r"成绩", r"考试", r"作业", r"论文", r"选课", r"课表", r"课程",
    r"学分", r"教授", r"导师", r"保研", r"奖学金", r"学费", r"签证", r"录取",
    r"社团", r"学生会", r"恋爱", r"股票", r"彩票",
)

_REPAIR_VOCAB = (
    "repair", "fix", "broken", "damage", "maintenance", "technician", "worker",
    "plumber", "electrician", "schedule", "dispatch", "ticket", "dorm", "college",
    "desk", "chair", "bed", "wardrobe", "air", "ac", "light", "socket", "power",
    "water", "leak", "drain", "shower", "toilet", "door", "lock", "window",
    "floor", "wall", "curtain", "network", "wifi", "heater", "washer", "tile",
    "报修", "维修", "修", "坏", "故障", "损坏", "漏水", "下水", "堵", "电路",
    "插座", "断电", "跳闸", "空调", "灯", "床", "桌", "椅", "衣柜", "门", "窗",
    "水管", "热水器", "洗衣机", "网络", "网口", "地板", "瓷砖", "窗帘", "花洒",
    "马桶", "书院", "宿舍", "师傅", "工单", "排班", "派工", "上门",
)


def is_off_topic(text):
    low = (text or "").lower()
    if not low.strip():
        return False
    for hint in _REPAIR_VOCAB:
        if hint in low:
            return False
    for pat in _OFF_TOPIC:
        if __import__("re").search(pat, low):
            return True
    return False


# --------------------------------------------------------------------------- #
# 速率限制 / Rate limiting
# --------------------------------------------------------------------------- #

_rate_lock = threading.Lock()
_rate_hits = {}


def rate_limited(ip):
    now = time.time()
    with _rate_lock:
        hits = [t for t in _rate_hits.get(ip, []) if now - t < RATE_LIMIT_WINDOW]
        if len(hits) >= RATE_LIMIT_MAX:
            _rate_hits[ip] = hits
            return int(RATE_LIMIT_WINDOW - (now - hits[0])) + 1
        hits.append(now)
        _rate_hits[ip] = hits
        if len(_rate_hits) > 5000:
            for k in [k for k, v in _rate_hits.items() if not v]:
                _rate_hits.pop(k, None)
    return 0


# --------------------------------------------------------------------------- #
# 登录尝试限制 / Sign-in throttling
#
# Only FAILED attempts count, and they count per (client, account) rather than
# per client: a student mistyping their own password must not lock out everyone
# else sitting behind the same campus NAT. Guessing one account is still capped,
# and the global limiter above caps the total request rate from any one address.
# --------------------------------------------------------------------------- #

AUTH_MAX_FAILURES = 10
AUTH_WINDOW = 300

_auth_lock = threading.Lock()
_auth_failures = {}


def _auth_key(ip, username):
    return "%s|%s" % (ip, (username or "").strip().lower())


def auth_throttled(ip, username):
    key = _auth_key(ip, username)
    now = time.time()
    with _auth_lock:
        fails = [t for t in _auth_failures.get(key, []) if now - t < AUTH_WINDOW]
        _auth_failures[key] = fails
        if len(fails) >= AUTH_MAX_FAILURES:
            return int(AUTH_WINDOW - (now - fails[0])) + 1
    return 0


def auth_note_failure(ip, username):
    key = _auth_key(ip, username)
    with _auth_lock:
        _auth_failures.setdefault(key, []).append(time.time())
        if len(_auth_failures) > 5000:
            for k in [k for k, v in _auth_failures.items() if not v]:
                _auth_failures.pop(k, None)


def auth_clear(ip, username):
    with _auth_lock:
        _auth_failures.pop(_auth_key(ip, username), None)


def body_lang(body):
    return body.get("lang") if body.get("lang") in ("zh", "en") else "zh"


_COMPOUND_SURNAMES = (
    "欧阳", "司马", "上官", "诸葛", "东方", "皇甫", "尉迟", "公孙",
    "慕容", "司徒", "令狐", "宇文", "长孙", "夏侯", "南宫",
)


def default_student_name(user, lang="zh"):
    """Localized ticket addressee derived from the signed-in account."""
    name = str(user.get("name") or "").strip()
    name_en = str(user.get("name_en") or "").strip()
    chinese = "".join(ch for ch in name if "\u4e00" <= ch <= "\u9fff")
    if lang == "zh":
        if chinese:
            surname = next((x for x in _COMPOUND_SURNAMES if chinese.startswith(x)),
                           chinese[0])
        else:
            surname = (name or name_en or user.get("username", "")).split()[0]
        return (surname + "同学") if surname else ""
    tokens = (name_en or name or user.get("username", "")).split()
    if not tokens:
        return ""
    # Seeded/localized Chinese names use family-name-first romanization.
    surname = tokens[0] if chinese and name_en else tokens[-1]
    return "Student " + surname


AGENT_ERRORS = {
    "not_found": ("未找到该受理会话。", "Agent session not found."),
    "forbidden": ("该受理会话或图片不属于当前账号。",
                  "That intake session or image is not yours."),
    "already_confirmed": ("该会话已经创建过工单。",
                          "This session already created a ticket."),
    "bad_agent_action": ("不支持该受理操作。", "Unsupported intake action."),
    "bad_draft": ("工单草稿格式无效。", "The ticket draft is invalid."),
    "bad_type": ("未知的维修类别。", "Unknown repair type."),
    "bad_priority": ("未知的紧急度。", "Unknown priority."),
    "session_resolved": ("该受理会话已经结束。", "This intake session is closed."),
    "bad_question_option": ("该选项当前不可用。", "That option is not valid."),
    "too_many_images": ("最多只能附加五张图片。", "At most five images are allowed."),
    "empty_text": ("请输入文字或附加图片。", "Text or images are required."),
    "draft_not_ready": ("工单草稿尚未补充完整。", "The ticket draft is not ready."),
}


def agent_error(code, lang):
    pair = AGENT_ERRORS.get(code, ("受理请求失败。", "The intake request failed."))
    return pair[0] if lang == "zh" else pair[1]


# --------------------------------------------------------------------------- #
# 排班编排 / Scheduling orchestration
#
# One rule runs through this whole section: a request may only ever touch the
# rows its session owns. Nothing here trusts a worker id, a ticket id or a
# "mine" list supplied by the browser.
# --------------------------------------------------------------------------- #

UNASSIGNED_WORKER = {
    "id": "", "name": "未指派", "name_en": "Unassigned", "worker_id": "",
    "trade_zh": "", "trade_en": "", "specialties": [], "certified": True, "phone": "",
}


def worker_record(worker_id):
    return STORE.worker(worker_id) or dict(UNASSIGNED_WORKER, id=worker_id or "")


def acting_worker_id(user):
    """Which technician record this account IS.

    The account stores the staff number (`worker_id`, also the login name) and
    the seeded record id (`worker_ref`). Resolve to a record id so ticket
    ownership compares like with like.
    """
    ref = user.get("worker_ref")
    if ref and STORE.worker(ref):
        return ref
    for w in STORE.workers():
        if ref and w.get("id") == ref:
            return w["id"]
        if w.get("worker_id") == user.get("worker_id") or w.get("worker_id") == user.get("username"):
            return w["id"]
    return ""


def assignment_map(day_iso):
    """`{ticket_id: worker_id}` for one day.

    Open tickets are split by the deterministic dispatcher; a ticket that was
    already picked up keeps the technician recorded on it, so history cannot be
    rewritten by a later roster change.
    """
    tickets = STORE.tickets()
    out = {}
    for wid, bucket in assign_day(day_iso, tickets, STORE.workers()):
        for t in bucket:
            out[t["id"]] = wid
    for t in tickets:
        if t.get("date") == day_iso and t.get("assigned_worker"):
            out[t["id"]] = t["assigned_worker"]
    return out


def day_context(day_iso, lang="zh"):
    """Assignment + appointed time for every ticket of one day.

    This is how the student side can say "陈师傅，周二 09:00-10:00 上门" without
    the student ever being able to read the rest of the queue.
    """
    tickets = STORE.tickets()
    ctx = {}
    assigned = assignment_map(day_iso)
    for worker in STORE.workers():
        wid = worker.get("id")
        bucket = [t for t in tickets
                  if t.get("date") == day_iso and assigned.get(t.get("id")) == wid]
        if not bucket:
            continue
        jobs, _unscheduled, _stats = schedule_day(day_iso, bucket, worker_record(wid), lang)
        for t in bucket:
            ctx[t["id"]] = {"worker_id": wid}
        for j in jobs:
            ctx[j["ticket_id"]].update({
                "start": j["start"].strftime("%H:%M"),
                "end": j["end"].strftime("%H:%M"),
                "seq": j["seq"],
            })
    for t in tickets:
        if t.get("date") == day_iso and t.get("assigned_worker"):
            ctx.setdefault(t["id"], {})["worker_id"] = t["assigned_worker"]
    return ctx


def worker_tickets(worker_id):
    """Every ticket the engine gave this technician, across all days."""
    tickets = STORE.tickets()
    days = sorted({t.get("date") for t in tickets if t.get("date")})
    out = []
    for day in days:
        amap = assignment_map(day)
        for t in tickets:
            if t.get("date") == day and amap.get(t["id"]) == worker_id:
                out.append(t)
    return out


def build_plan(day_iso, worker_id, lang="zh"):
    """Run the engine for one (worker, day) pair — never for anybody else."""
    worker = worker_record(worker_id)
    tickets = [t for t in STORE.tickets() if t.get("date") == day_iso]
    assigned = assignment_map(day_iso)
    mine = [t for t in tickets if assigned.get(t.get("id")) == worker_id]
    pool = len([t for t in tickets if assigned.get(t.get("id"))])
    jobs, unscheduled, stats = schedule_day(day_iso, mine, worker, lang)
    stats = dict(stats, assigned=len(mine), pool=pool)
    return worker, jobs, unscheduled, stats


def unscheduled_view(t, reason, lang="zh"):
    """A ticket the engine could not fit into this technician's day.

    The technician is being asked to make a judgement — squeeze it in on the
    way past, or ask the student to rebook — so this carries everything a
    scheduled stop carries, plus WHY it did not fit and when the student said
    they were free. Deciding without the address, the contact number and the
    declared window would just be guessing.
    """
    rtype = REPAIR_BY_ID.get(t.get("repair_type")) or REPAIR_BY_ID["other"]
    day = t.get("date", "")
    windows = []
    for pair in (t.get("slots") or []):
        try:
            start, end = int(pair[0]), int(pair[1])
        except Exception:
            continue
        if 0 <= start <= end <= 20:
            windows.append("%s-%s" % (slot_label(day, start, lang).split("-")[0],
                                      slot_label(day, end, lang).split("-")[1]))
    return {
        "ticket_id": t["id"],
        "college": t.get("college", ""),
        "college_label": college_label(t.get("college", ""), lang),
        "block": t.get("block", ""),
        "dorm": t.get("dorm", ""),
        "location": " ".join(x for x in (t.get("block"), t.get("dorm")) if x),
        "public": bool(t.get("public")),
        "repair_type": t.get("repair_type", "other"),
        "repair_label": rtype["zh"] if lang == "zh" else rtype["en"],
        "duration_minutes": rtype["minutes"],
        "parts": rtype.get("parts" if lang == "zh" else "parts_en", ""),
        "priority": t.get("priority", "normal"),
        "priority_label": PRIORITY_LEVELS.get(t.get("priority", "normal"),
                                             PRIORITY_LEVELS["normal"])[lang],
        "student": t.get("student_name", ""),
        "contact": t.get("contact", ""),
        "description": t.get("description", ""),
        "date": day,
        "windows": windows,
        "reason": reason,
        "status": t.get("status", "pending"),
        # Already answered? The console shows the decision instead of the
        # buttons, so nobody has to remember what they did ten minutes ago.
        "off_route": bool(t.get("off_route")),
        "reschedule_requested": bool(t.get("reschedule_requested")),
    }


def plan_payload(day_iso, worker_id, lang="zh"):
    worker, jobs, unscheduled, stats = build_plan(day_iso, worker_id, lang)
    return {
        "date": day_iso,
        "worker": {
            "id": worker.get("id"),
            "name": worker.get("name"),
            "name_en": worker.get("name_en", ""),
            "worker_id": worker.get("worker_id", ""),
            "trade_zh": worker.get("trade_zh", ""),
            "trade_en": worker.get("trade_en", ""),
            "specialties": worker.get("specialties", []),
            "certified": worker.get("certified", False),
        },
        "jobs": [job_to_json(j, lang) for j in jobs],
        "unscheduled": [unscheduled_view(t, reason, lang)
                        for t, reason in unscheduled],
        "stats": stats,
    }


# --------------------------------------------------------------------------- #
# 工单视图 / Ticket projection
# --------------------------------------------------------------------------- #

#: Where a ticket sits in its life cycle. The student drives the last step —
#: that is the "反馈" half of the system, which the first version simply did not
#: have: a job was either pending or done, and nobody ever confirmed it was
#: actually fixed.
STATUS_FLOW = ("pending", "accepted", "done", "confirmed")

#: Bilingual labels resolved on the server, same as every other label, so the
#: two consoles and the API always agree on what a status is called.
STATUS_LABELS = {
    "pending":   {"zh": "待受理", "en": "Pending"},
    "accepted":  {"zh": "已接单", "en": "Accepted"},
    "done":      {"zh": "已完工", "en": "Completed"},
    "confirmed": {"zh": "已验收", "en": "Confirmed"},
}

# Persisted status values remain unchanged for old data/tests. These stages
# expose the requested five-step business wording without a destructive migration.
STATUS_STAGES = {
    "pending": {"id": "pending", "zh": "待受理", "en": "Pending"},
    "accepted": {"id": "assigned", "zh": "已派单 / 维修中", "en": "Assigned / In progress"},
    "done": {"id": "awaiting_confirmation", "zh": "已完成待确认", "en": "Completed, awaiting confirmation"},
    "confirmed": {"id": "closed", "zh": "关闭", "en": "Closed"},
}

#: Which statuses each role is allowed to move a ticket to.
STATUS_ALLOWED = {
    ROLE_STUDENT: {"confirmed", "pending"},      # 验收 / 重新打开
    ROLE_WORKER: {"accepted", "done"},           # 接单 / 完工
}

#: 排队状态 / Where a ticket actually stands, as the student sees it.
#:
#: "有个师傅负责" and "有个确定的上门时间" are two different facts, and the
#: student needs both. A job can be assigned to somebody whose day is already
#: full, and the honest answer then is "waiting on the technician", not
#: "waiting on the office" — which is what `unplaced` says.
QUEUE_LABELS = {
    "unassigned": {"zh": "等待派单", "en": "Awaiting assignment"},
    "unplaced":   {"zh": "当日路线已满，师傅会另行安排",
                   "en": "Today's route is full — the technician will arrange another time"},
    "scheduled":  {"zh": "已排入当日路线", "en": "On today's route"},
    "off_route":  {"zh": "师傅会顺路过去处理", "en": "The technician will come by en route"},
    "reschedule": {"zh": "师傅建议改约", "en": "The technician suggests a new time"},
}


def _worker_public(worker, lang, with_phone=False):
    if not worker or not worker.get("id"):
        return None
    out = {
        "id": worker.get("id", ""),
        "name": worker.get("name", ""),
        "name_en": worker.get("name_en", ""),
        "worker_id": worker.get("worker_id", ""),
        "trade": worker.get("trade_zh", "") if lang == "zh" else worker.get("trade_en", ""),
        "certified": bool(worker.get("certified")),
    }
    # A student never needs the internal extension; a technician may need to
    # reach a colleague. Keeping it out of the student payload means the number
    # is not merely hidden in the UI.
    if with_phone:
        out["phone"] = worker.get("phone", "")
    return out


def ticket_view(t, user, lang="zh", ctx=None):
    """One ticket, projected for the role that is asking."""
    rtype = REPAIR_BY_ID.get(t.get("repair_type")) or REPAIR_BY_ID["other"]
    is_worker = user["role"] == ROLE_WORKER
    entry = (ctx or {}).get(t["id"]) or {}
    wid = entry.get("worker_id") or t.get("assigned_worker") or ""
    status = t.get("status", "pending")

    out = {
        **t,
        "repair_label": rtype["zh"] if lang == "zh" else rtype["en"],
        "college_label": college_label(t.get("college", ""), lang),
        "college_blocks": college_blocks_text(t.get("college", ""), lang),
        "location": " ".join(x for x in (t.get("block"), t.get("dorm")) if x),
        "duration_minutes": rtype["minutes"],
        "priority_label": PRIORITY_LEVELS.get(t.get("priority", "normal"),
                                             PRIORITY_LEVELS["normal"])[lang],
        "status": status,
        "status_label": STATUS_LABELS[status][lang],
        "status_stage": STATUS_STAGES.get(status, STATUS_STAGES["pending"])["id"],
        "status_stage_label": STATUS_STAGES.get(status, STATUS_STAGES["pending"])[lang],
        "assigned_worker": _worker_public(worker_record(wid) if wid else None,
                                          lang, with_phone=is_worker),
        "appointment": ({"start": entry["start"], "end": entry["end"],
                         "seq": entry.get("seq")}
                        if entry.get("start") else None),
    }
    # Internal idempotency metadata is never part of any role's ticket view.
    for internal in ("admin_client_request_id", "admin_request_fingerprint",
                     "admin_created_by"):
        out.pop(internal, None)
    # ---- 排队状态 / where it stands -------------------------------------- #
    # Ordered: an explicit technician decision outranks the engine's output,
    # because it is newer and it is a human's call about the physical world.
    if t.get("off_route"):
        queue_state = "off_route"
    elif t.get("reschedule_requested"):
        queue_state = "reschedule"
    elif entry.get("start"):
        queue_state = "scheduled"
    elif wid:
        queue_state = "unplaced"
    else:
        queue_state = "unassigned"
    out["queue_state"] = queue_state
    out["queue_label"] = QUEUE_LABELS[queue_state][lang]
    out["suggested_date"] = t.get("suggested_date", "")
    out["reschedule_note"] = t.get("reschedule_note", "")

    # The credit ledger: a finished job carries the student's own verdict.
    out["rating"] = t.get("rating") or 0
    out["feedback"] = t.get("feedback", "")
    out["image_ids"] = list(t.get("image_ids") or [])
    out["completion_image_ids"] = list(t.get("completion_image_ids") or [])
    out["completed_at"] = (t.get("completed_at") if "completed_at" in t
                           else t.get("done_at"))
    out["completed_by"] = (t.get("completed_by") if "completed_by" in t
                           else t.get("assigned_worker") or "")
    return out


def visible_tickets(user, lang="zh"):
    """Exactly the rows this account may read. The filter is server-side."""
    if user["role"] == ROLE_WORKER:
        raw = worker_tickets(acting_worker_id(user))
    else:
        raw = [t for t in STORE.tickets() if t.get("user_id") == user["id"]]

    raw.sort(key=lambda x: x.get("created_at", ""), reverse=True)
    ctx_cache = {}
    out = []
    for t in raw:
        day = t.get("date", "")
        if day not in ctx_cache:
            ctx_cache[day] = day_context(day, lang)
        out.append(ticket_view(t, user, lang, ctx_cache[day]))
    return out


# --------------------------------------------------------------------------- #
# 管理员端 / Administrator side
#
# The third role exists for a different job from the other two: it neither
# files as a student nor performs work, so it owns no tickets and no route.
# Dedicated admin endpoints manage the whole board and accounts without
# weakening either role's private interface.
#
# The projection below is deliberately separate from `ticket_view`: an
# administrator needs the owner's identity on every row (to answer "whose
# request is this?"), which is exactly the field the other two roles must
# never be handed.
# --------------------------------------------------------------------------- #

#: Stands in for "the caller" when projecting rows for an administrator.
_ADMIN_PRINCIPAL = {"id": "", "role": ROLE_ADMIN}


def admin_worker_view(w, lang="zh"):
    """A technician record, complete — including the internal extension."""
    specs = list(w.get("specialties") or [])
    return {
        "id": w.get("id", ""),
        "worker_id": w.get("worker_id", ""),
        "name": w.get("name", ""),
        "name_en": w.get("name_en", ""),
        "trade": w.get("trade_zh", "") if lang == "zh" else w.get("trade_en", ""),
        "trade_zh": w.get("trade_zh", ""),
        "trade_en": w.get("trade_en", ""),
        "specialties": specs,
        "specialty_labels": [repair_label(s, lang) for s in specs],
        "certified": bool(w.get("certified")),
        "phone": w.get("phone", ""),
    }


def admin_user_view(u, lang="zh", ticket_counts=None):
    """One account, minus the password digest, plus what it owns."""
    counts = ticket_counts or {}
    out = {
        "id": u.get("id", ""),
        "role": u.get("role", ""),
        "username": u.get("username", ""),
        "name": u.get("name", ""),
        "name_en": u.get("name_en", ""),
        # Account contact number.  This is distinct from a worker roster's
        # internal extension, which is merged below only for worker rows.
        "phone": u.get("phone", "") if u.get("role") == ROLE_STUDENT else "",
        "college": u.get("college", ""),
        "college_label": college_label(u.get("college", ""), lang) if u.get("college") else "",
        "block": u.get("block", ""),
        "dorm": u.get("dorm", ""),
        "worker_id": u.get("worker_id", ""),
        "worker_ref": u.get("worker_ref", ""),
        "created_at": u.get("created_at", ""),
        "ticket_count": counts.get(u.get("id", ""), 0),
    }
    ref = u.get("worker_ref")
    if ref:
        record = STORE.worker(ref)
        if record:
            wv = admin_worker_view(record, lang)
            # The ACCOUNT is the authority on identity: `id` is what the desk
            # acts on, and `name` may have been corrected on the account since
            # the roster record was written. Only the roster's own fields are
            # borrowed, never its record id.
            for key in ("id", "name", "name_en", "worker_id"):
                wv.pop(key, None)
            out.update(wv)
    return out


def all_tickets_view(lang="zh"):
    """Every ticket in the system, with the owner on the row."""
    raw = STORE.tickets()
    raw.sort(key=lambda x: x.get("created_at", ""), reverse=True)
    ctx_cache = {}
    out = []
    for t in raw:
        day = t.get("date", "")
        if day not in ctx_cache:
            ctx_cache[day] = day_context(day, lang)
        out.append(ticket_view(t, _ADMIN_PRINCIPAL, lang, ctx_cache[day]))
    return out


def admin_overview(lang="zh"):
    """The one screen an operator wants first: what is the state of the system."""
    tickets = STORE.tickets()
    users = STORE.users()
    workers = STORE.workers()
    today = date.today().isoformat()

    by_status, by_priority, by_college = {}, {}, {}
    rated, rating_sum = 0, 0
    for t in tickets:
        status = t.get("status", "pending")
        by_status[status] = by_status.get(status, 0) + 1
        pri = t.get("priority", "normal")
        by_priority[pri] = by_priority.get(pri, 0) + 1
        col = t.get("college", "")
        by_college[col] = by_college.get(col, 0) + 1
        rating = t.get("rating") or 0
        if rating:
            rated += 1
            rating_sum += rating

    roles = {}
    for u in users:
        r = u.get("role", "")
        roles[r] = roles.get(r, 0) + 1

    return {
        "date_today": today,
        "totals": {
            "tickets": len(tickets),
            "students": roles.get(ROLE_STUDENT, 0),
            "workers": roles.get(ROLE_WORKER, 0),
            "admins": roles.get(ROLE_ADMIN, 0),
            "roster": len(workers),
        },
        "by_status": by_status,
        "by_priority": by_priority,
        "by_college": [
            {"college": c, "label": college_label(c, lang), "count": n}
            for c, n in sorted(by_college.items(), key=lambda kv: (-kv[1], kv[0]))
        ],
        "today": {
            "tickets": sum(1 for t in tickets if t.get("date") == today),
            "open": sum(1 for t in tickets
                        if t.get("date") == today
                        and t.get("status") not in ("done", "confirmed")),
        },
        "feedback": {
            "rated": rated,
            "average": round(rating_sum / rated, 2) if rated else 0,
        },
        "workers": [admin_worker_view(w, lang) for w in workers],
        "status_labels": {k: v[lang] for k, v in STATUS_LABELS.items()},
        "queue_labels": {k: v[lang] for k, v in QUEUE_LABELS.items()},
        "priority_labels": {k: v[lang] for k, v in PRIORITY_LEVELS.items()},
    }



def model_status():
    """What an administrator may see about the model key. Never the key itself."""
    source = Handler.key_source or ""
    provider = Handler.provider or ""
    base = ""
    if provider == "custom":
        base = ai_agents.configured_base()
    return {
        "configured": bool(Handler.api_key),
        "source": source,
        "locked": source == "env",
        "provider": provider,
        "model": Handler.model or "",
        "base_url": base,
    }


# --------------------------------------------------------------------------- #
# HTTP handler
# --------------------------------------------------------------------------- #

class Handler(BaseHTTPRequestHandler):
    server_version = "DormCare/1.0"
    protocol_version = "HTTP/1.1"

    api_key = ""
    key_source = ""            # "env" | "enrolled" | ""
    provider = ""
    model = DEFAULT_MODEL

    # ---- plumbing --------------------------------------------------------- #

    def _prepare(self):
        """Per-request state: who is calling, and any headers we must add."""
        self._extra_headers = []
        self.user = self._current_user()
        return self.user

    def _send(self, status, body, ctype="application/json; charset=utf-8"):
        if isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "same-origin")
        for name, value in getattr(self, "_extra_headers", []):
            self.send_header(name, value)
        self.end_headers()
        if getattr(self, "_head_only", False):
            return          # Content-Length stays honest; the body is omitted
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _json(self, status, obj):
        self._send(status, json.dumps(obj, ensure_ascii=False))

    def _err(self, status, code, message, lang=None):
        payload = {"error": message, "code": code}
        if lang in ("zh", "en"):
            payload["lang"] = lang
        self._json(status, payload)

    def log_message(self, fmt, *args):
        sys.stderr.write("[%s] %s\n" % (self.log_date_time_string(), fmt % args))

    def _read_body(self):
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = 0
        if length <= 0 or length > MAX_BODY_BYTES:
            self.close_connection = True
            return None
        try:
            return json.loads(self.rfile.read(length).decode("utf-8"))
        except Exception:
            return "bad_json"

    # ---- identity -------------------------------------------------------- #
    #
    # The session is resolved once per request from the cookie. Everything that
    # decides what a caller may see reads `self.user`; no handler ever looks at
    # a client-supplied id to work out who the caller is.

    def _session_token(self):
        raw = self.headers.get("Cookie")
        if not raw:
            return ""
        try:
            jar = SimpleCookie()
            jar.load(raw)
        except Exception:
            return ""
        morsel = jar.get(SESSION_COOKIE)
        return morsel.value if morsel else ""

    def _current_user(self):
        """The signed-in user, or None. Expired sessions are dropped on sight."""
        token = self._session_token()
        session = STORE.session(token)
        if not session:
            return None
        if session_expired(session):
            STORE.delete_session(token)
            return None
        user = STORE.user(session.get("user_id"))
        if not user:
            STORE.delete_session(token)
            return None
        return user

    def _require(self, *roles):
        """`self.user` if authorised, else send the error and return None."""
        user = self.user
        if not user:
            self._err(401, "auth_required", "Please sign in to continue.")
            return None
        if roles and user["role"] not in roles:
            self._err(403, "forbidden",
                      "This area is for %s accounts." % " / ".join(roles))
            return None
        return user

    def _set_session_cookie(self, session):
        cookie = ("%s=%s; Path=/; HttpOnly; SameSite=Lax; Max-Age=%d"
                  % (SESSION_COOKIE, session["token"], SESSION_DAYS * 24 * 3600))
        if COOKIE_SECURE or self._forwarded_proto() == "https":
            cookie += "; Secure"
        self._extra_headers.append(("Set-Cookie", cookie))

    def _clear_session_cookie(self):
        self._extra_headers.append((
            "Set-Cookie",
            "%s=; Path=/; HttpOnly; SameSite=Lax; Max-Age=0" % SESSION_COOKIE))

    def _redirect(self, location):
        self._extra_headers.append(("Location", location))
        self._send(303, b"", "text/plain; charset=utf-8")

    # ---- static ----------------------------------------------------------- #

    def do_GET(self):
        self._prepare()
        path = posixpath.normpath(self.path.split("?", 1)[0])
        params = dict(p.split("=", 1) for p in
                      (self.path.split("?", 1)[1].split("&") if "?" in self.path else [])
                      if "=" in p)
        lang = params.get("lang") if params.get("lang") in ("zh", "en") else "zh"

        # ---- public: service state --------------------------------------- #
        if path == "/api/health":
            self._json(200, {
                "ok": True,
                "app": "DormCare",
                "model": self.model,
                "key_configured": bool(self.api_key),
                "enrollable": (self.key_source != "env" and not self.api_key
                               and bool(self.user) and self.user.get("role") == ROLE_ADMIN),
                "provider": self.provider or "",
                "tickets": len(STORE.tickets()),
                "workers": len(STORE.workers()),
                "date_today": date.today().isoformat(),
                "signed_in": bool(self.user),
            })
            return

        # ---- public: the fixed knowledge base ---------------------------- #
        # Colleges, repair types and travel rules are constants, identical for
        # every visitor and containing no personal data, so the sign-in page can
        # read them too (it needs the college list for the registration form).
        if path == "/api/config":
            self._json(200, {
                "colleges": [
                    {"id": c["id"], "zh": c["zh"], "en": c["en"],
                     "blocks": c["blocks"],
                     "blocks_zh": c["blocks_zh"], "blocks_en": c["blocks_en"]}
                    for c in COLLEGES
                ],
                "repair_types": [
                    {"id": r["id"], "zh": r["zh"], "en": r["en"], "minutes": r["minutes"],
                     "zh_note": r["zh_note"], "en_note": r["en_note"],
                     "certified": bool(r.get("certified"))}
                    for r in REPAIR_TYPES
                ],
                "travel_rules": TRAVEL_RULES,
                "priorities": PRIORITY_LEVELS,
                "slot_minutes": SLOT_MINUTES,
                "days": [d.isoformat() for d in
                         next_workdays(date.today(), TRAVEL_RULES["scheduling_days"])],
            })
            return

        # ---- identity ----------------------------------------------------- #
        if path == "/api/auth/me":
            if not self.user:
                self._json(401, {"authenticated": False})
                return
            self._json(200, {"authenticated": True, "user": public_user(self.user)})
            return

        # ---- role-scoped data --------------------------------------------- #
        if path == "/api/tickets":
            # Students and technicians only: each of them sees a slice, and the
            # slice is chosen here. An administrator has its own view
            # (/api/admin/tickets) precisely so this one stays a *slice*.
            user = self._require(ROLE_STUDENT, ROLE_WORKER)
            if not user:
                return
            self._json(200, {"tickets": visible_tickets(user, lang),
                             "role": user["role"]})
            return

        if path.startswith("/api/uploads/"):
            user = self._require(ROLE_STUDENT, ROLE_WORKER, ROLE_ADMIN)
            if not user:
                return
            upload = STORE.upload(path.rsplit("/", 1)[-1])
            if not upload:
                self._err(404, "not_found", "Image not found.")
                return
            if not self._can_read_upload(upload, user):
                self._err(403, "forbidden", "You cannot read this image.")
                return
            full = os.path.join(UPLOAD_DIR, os.path.basename(upload.get("filename", "")))
            if not os.path.isfile(full):
                self._err(404, "not_found", "Image file not found.")
                return
            with open(full, "rb") as fh:
                self._send(200, fh.read(), upload.get("mime", "application/octet-stream"))
            return

        if path == "/api/agent/session":
            user = self._require(ROLE_STUDENT)
            if not user:
                return
            lang = params.get("lang") if params.get("lang") in ("zh", "en") else "zh"
            session = STORE.agent_session(params.get("id"))
            if not session:
                self._err(404, "not_found", agent_error("not_found", lang), lang)
                return
            if session.get("user_id") != user["id"]:
                self._err(403, "forbidden", agent_error("forbidden", lang), lang)
                return
            view = self._agent_session_view(session)
            self._json(200, {"lang": view["lang"], "session": view})
            return

        if path == "/api/workers":
            user = self._require(ROLE_STUDENT, ROLE_WORKER, ROLE_ADMIN)
            if not user:
                return
            # Names, trades and staff numbers are fine; the internal extension
            # is not handed to students.
            with_phone = user["role"] in (ROLE_WORKER, ROLE_ADMIN)
            self._json(200, {"workers": [
                _worker_public(w, lang, with_phone=with_phone) for w in STORE.workers()
            ]})
            return

        # ---- administrator: the whole board -------------------------------- #
        if path == "/api/admin/overview":
            user = self._require(ROLE_ADMIN)
            if not user:
                return
            self._json(200, admin_overview(lang))
            return

        if path == "/api/admin/model":
            user = self._require(ROLE_ADMIN)
            if not user:
                return
            self._json(200, model_status())
            return

        if path == "/api/admin/tickets":
            user = self._require(ROLE_ADMIN)
            if not user:
                return
            self._json(200, {"tickets": all_tickets_view(lang)})
            return

        if path == "/api/admin/audit":
            user = self._require(ROLE_ADMIN)
            if not user:
                return
            try:
                limit = max(1, min(200, int(params.get("limit") or 50)))
            except (TypeError, ValueError):
                self._err(400, "bad_limit", "Audit limit must be an integer.")
                return
            self._json(200, {"audit": STORE.admin_audit(limit), "limit": limit})
            return

        if path == "/api/admin/users":
            user = self._require(ROLE_ADMIN)
            if not user:
                return
            counts = {}
            for t in STORE.tickets():
                uid = t.get("user_id", "")
                counts[uid] = counts.get(uid, 0) + 1
            people = sorted(STORE.users(),
                            key=lambda u: (u.get("role", ""), u.get("username", "")))
            self._json(200, {"users": [admin_user_view(u, lang, counts) for u in people]})
            return

        if path == "/api/plan":
            user = self._require(ROLE_WORKER)
            if not user:
                return
            day = params.get("date") or date.today().isoformat()
            try:
                date.fromisoformat(day)
            except Exception:
                self._err(400, "bad_date", "Invalid date.")
                return
            # The worker parameter is accepted but ignored: your session decides
            # whose day this is.
            self._json(200, plan_payload(day, acting_worker_id(user), lang))
            return

        # ---- pages -------------------------------------------------------- #
        if path in ENTRY_PATHS:
            if not self.user:
                self._redirect("/login.html")
            else:
                self._redirect(HOME_BY_ROLE[self.user["role"]])
            return

        entry = STATIC_FILES.get(path)
        if entry is None:
            self._err(404, "not_found", "not found")
            return
        filename, ctype, needed = entry
        if needed is not None:
            if not self.user:
                self._redirect("/login.html")
                return
            # ANY_ROLE is the main menu: everybody signed in may open it, and
            # it is the menu that decides which console this person may enter.
            if needed != ANY_ROLE and self.user["role"] != needed:
                self._redirect(HOME_BY_ROLE[self.user["role"]])
                return
        full = os.path.abspath(os.path.join(STATIC_ROOT, filename))
        # STATIC_FILES is an explicit URL whitelist. Keep a root containment
        # check as a second line of defence if an entry is edited later.
        if (os.path.commonpath((STATIC_ROOT, full)) != STATIC_ROOT
                or any(part.startswith(".") for part in filename.split("/"))
                or not os.path.isfile(full)):
            self._err(404, "not_found", "not found")
            return
        with open(full, "rb") as fh:
            self._send(200, fh.read(), ctype)

    def do_HEAD(self):
        # A HEAD must not carry a body — delegate, then drop it.
        self._head_only = True
        try:
            self.do_GET()
        finally:
            self._head_only = False

    def _can_read_upload(self, upload, user):
        if user["role"] == ROLE_ADMIN:
            return True
        if upload.get("owner_user_id") == user["id"] and user["role"] == ROLE_STUDENT:
            return True
        tid = upload.get("ticket_id")
        ticket = STORE.ticket(tid) if tid else None
        if not ticket:
            return False
        if user["role"] == ROLE_STUDENT:
            return ticket.get("user_id") == user["id"]
        return assignment_map(ticket.get("date", "")).get(tid) == acting_worker_id(user)

    @staticmethod
    def _agent_session_view(session):
        view = {k: session.get(k) for k in (
            "id", "created_at", "updated_at", "lang", "history", "image_ids",
            "draft", "ready", "stage", "detected", "missing", "self_help",
            "safety_warning", "resolved", "ticket_id", "route_options",
            "emergency", "confidence", "kb_status", "solution",
            "next_question", "question_options", "state", "response_mode",
            "active_route", "agent_status")}
        # Legacy sessions may contain the removed human-handoff state/option.
        routes = [
            item for item in (view.get("route_options") or [])
            if isinstance(item, dict) and item.get("id") in ("self_help", "smart_repair")
        ]
        if view.get("state") == "human_handoff" or view.get("stage") == "human_handoff":
            view.update({
                "state": "route_choice", "stage": "route_choice",
                "active_route": "", "agent_status": "ai", "ready": False,
                "next_question": None, "question_options": [], "missing": [],
            })
            routes = []
        view["lang"] = view.get("lang") if view.get("lang") in ("zh", "en") else "zh"
        if view.get("state") == "route_choice":
            routes = [
                {"id": "self_help", "enabled": True, "recommended": False},
                {"id": "smart_repair", "enabled": True,
                 "recommended": bool(view.get("emergency"))},
            ]
        elif view.get("state") in ("advice", "no_solution"):
            routes = [{"id": "smart_repair", "enabled": True,
                       "recommended": bool(view.get("emergency"))}]
        view["route_options"] = routes
        return view

    # ---- POST ------------------------------------------------------------- #

    def do_POST(self):
        self._prepare()
        path = self.path.split("?", 1)[0]
        body = self._read_body()
        if body is None:
            self._err(413, "bad_size", "invalid request size")
            return
        if body == "bad_json":
            self._err(400, "bad_json", "invalid JSON body")
            return

        ip = self.client_address[0] if self.client_address else "unknown"

        # Sign-in is the one endpoint genuinely worth brute-forcing, so wrong
        # passwords get their own, much tighter budget on top of the global one.
        if path in ("/api/auth/login", "/api/auth/register"):
            wait = auth_throttled(ip, body.get("username"))
            if wait:
                self._err(429, "auth_too_many",
                          "Too many attempts. Please wait %d seconds." % wait)
                return

        wait = rate_limited(ip)
        if wait:
            self._err(429, "too_many", "Too many requests. Please wait %d seconds." % wait)
            return

        # A cross-site page cannot send `application/json`, and a SameSite=Lax
        # cookie is not attached to a cross-site POST — this check is simply
        # the third lock on the same door.
        if not self._same_origin():
            self._err(403, "bad_origin", "Cross-site request refused.")
            return

        routes = {
            "/api/auth/login": self._api_login,
            "/api/auth/logout": self._api_logout,
            "/api/auth/register": self._api_register,
            "/api/setup": self._api_setup,
            "/api/setup/reset": self._api_setup_reset,
            # ---- self-service: nobody has to wait for the administrator ---- #
            "/api/account/room": self._api_account_room,
            "/api/account/phone": self._api_account_phone,
            "/api/account/password": self._api_account_password,
            "/api/intake/parse": self._api_intake_parse,
            "/api/intake/advice": self._api_intake_advice,
            "/api/uploads": self._api_uploads,
            "/api/agent/session": self._api_agent_session,
            "/api/agent/message": self._api_agent_message,
            "/api/agent/confirm": self._api_agent_confirm,
            "/api/agent/event": self._api_agent_event,
            "/api/tickets": self._api_create_ticket,
            "/api/tickets/status": self._api_ticket_status,
            "/api/tickets/feedback": self._api_ticket_feedback,
            "/api/tickets/delete": self._api_ticket_delete,
            "/api/tickets/offroute": self._api_ticket_offroute,
            "/api/tickets/reschedule": self._api_ticket_reschedule,
            "/api/schedule/optimize": self._api_optimize,
            "/api/dispatch/advice": self._api_dispatch_advice,
            # ---- administrator: the account desk ---------------------------- #
            "/api/admin/user/update": self._api_admin_user_update,
            "/api/admin/user/password": self._api_admin_user_password,
            "/api/admin/user/create": self._api_admin_user_create,
            "/api/admin/worker/create": self._api_admin_worker_create,
            "/api/admin/ticket/create": self._api_admin_ticket_create,
            "/api/admin/ticket/update": self._api_admin_ticket_update,
            "/api/admin/ticket/delete": self._api_admin_ticket_delete,
        }
        fn = routes.get(path)
        if fn is None:
            self._err(404, "not_found", "not found")
            return
        try:
            fn(body, ip)
        except AIError as exc:
            self._handle_ai_error(exc)
        except Exception as exc:
            sys.stderr.write("[error] %s\n" % (type(exc).__name__,))
            self._err(500, "server", "Something went wrong on the server.")

    def _same_origin(self):
        origin = self.headers.get("Origin")
        if not origin or origin == "null":
            return True                      # curl / same-origin fetch
        origin = origin.strip().rstrip("/")
        parsed = urlsplit(origin)
        if parsed.scheme not in ("http", "https") or not parsed.netloc:
            return False

        # An explicitly configured public origin is authoritative and is the
        # reliable path when a hosting provider rewrites Host internally.
        if origin in PUBLIC_ORIGINS:
            return True

        host = (self.headers.get("Host") or "").strip()
        if parsed.netloc.lower() == host.lower():
            return True

        # Reverse-proxy headers are useful only when the deployment explicitly
        # opts in. Take the first value: proxies append hops from left to right.
        if TRUST_PROXY:
            forwarded_host = (self.headers.get("X-Forwarded-Host") or "").split(",", 1)[0].strip()
            forwarded_proto = self._forwarded_proto()
            if forwarded_host and parsed.netloc.lower() == forwarded_host.lower():
                return not forwarded_proto or parsed.scheme == forwarded_proto
        return False

    def _forwarded_proto(self):
        if not TRUST_PROXY:
            return ""
        proto = (self.headers.get("X-Forwarded-Proto") or "").split(",", 1)[0].strip().lower()
        return proto if proto in ("http", "https") else ""

    # ---- sign in / out ---------------------------------------------------- #

    def _api_login(self, body, ip):
        username = clean_username(body.get("username"))
        password = body.get("password") or ""

        user = STORE.user_by_username(username)
        # One message for every failure, and verify_password runs even when the
        # account is missing — so the endpoint cannot be used to enumerate
        # which student numbers exist.
        ok = verify_password(password, user["password"] if user else hash_password("x"))
        if not user or not ok:
            auth_note_failure(ip, username)
            self._err(401, "auth_bad_credentials", "Incorrect account or password.")
            return
        auth_clear(ip, username)
        session = new_session(user)
        STORE.add_session(session)
        self._set_session_cookie(session)
        self._json(200, {"ok": True, "user": public_user(user),
                         "home": HOME_BY_ROLE.get(user["role"], MENU_PATH)})

    def _api_logout(self, body, ip):
        token = self._session_token()
        if token:
            STORE.delete_session(token)
        self._clear_session_cookie()
        self._json(200, {"ok": True})

    def _api_register(self, body, ip):
        """Student self-registration.

        Technicians are NOT self-registered: an account that can read other
        people's rooms and phone numbers must be issued, not claimed.

        The room is collected here because it is the address every future
        ticket gets filed against — asking for it once, at sign-up, is what
        stops a repair request from claiming to come from anywhere.
        """
        username = clean_username(body.get("username"))
        password = body.get("password") or ""
        name = clean_name(body.get("name"))
        college = body.get("college") or ""
        phone, phone_code = normalize_student_phone(body.get("phone"), required=True)

        code = username_error(username) or password_error(password)
        if code:
            self._err(400, code, "That account or password is not valid.")
            return
        if not name:
            self._err(400, "auth_missing_name", "Please give your name.")
            return
        if phone_code:
            self._err(400, phone_code, "Please give a valid mainland China mobile number.")
            return
        # Availability is checked before the address: "that account already
        # exists" is the more useful of the two answers, and it is the one the
        # caller can act on immediately.
        if STORE.user_by_username(username):
            auth_note_failure(ip, username)
            self._err(409, "auth_username_taken", "That account already exists.")
            return
        if college not in COLLEGE_BY_ID:
            self._err(400, "auth_missing_college", "Please choose your college.")
            return
        # A college whose towers are only a door-side placeholder has nothing
        # to pick, so the tower is required exactly when there is a real list.
        block = normalize_block(college, body.get("block"))
        if college_real_blocks(college) and not block:
            self._err(400, "auth_need_block", "Please choose your tower.")
            return
        dorm = (body.get("dorm") or "").strip()[:MAX_DORM]
        if not dorm:
            self._err(400, "auth_missing_dorm", "Please give your room number.")
            return

        user = STORE.add_user({
            "role": ROLE_STUDENT,
            "username": username,
            "name": name,
            "name_en": "",
            "phone": phone,
            "college": college,
            "block": block,
            "dorm": dorm,
            "worker_id": "",
            "worker_ref": "",
            "password": hash_password(password),
        })
        session = new_session(user)
        STORE.add_session(session)
        self._set_session_cookie(session)
        self._json(200, {"ok": True, "user": public_user(user), "created": True,
                         "home": HOME_BY_ROLE[ROLE_STUDENT]})

    # ---- self-service: the account desk, minus the desk -------------------- #
    #
    # Two things a person must be able to do for themselves, without waiting
    # for the administrator:
    #
    #   * correct the room on their own account — people move, and a room typed
    #     wrong at sign-up silently mis-files every future repair request;
    #   * change their own password.
    #
    # Both act on `self.user` only. There is no `id` in the request that could
    # name somebody else's account, so these two endpoints are structurally
    # incapable of an IDOR — which is exactly why they can be open to every
    # signed-in role rather than gated behind the administrator.

    def _api_account_phone(self, body, ip):
        """Update only the signed-in student's account contact number."""
        user = self._require(ROLE_STUDENT)
        if not user:
            return
        phone, code = normalize_student_phone(body.get("phone"), required=True)
        if code:
            self._err(400, code, "Please give a valid mainland China mobile number.")
            return
        updated = STORE.update_user(user["id"], {"phone": phone})
        self._json(200, {"ok": True, "user": public_user(updated)})

    def _api_account_room(self, body, ip):
        """Correct the room on the caller's own account.

        The room is the address every future ticket is filed against, so this
        takes effect immediately: the next request lands at the new address,
        and tickets already filed keep the address they were filed with — a
        repair request is a record of where the fault WAS, and rewriting it
        would send a technician who was on the way to the wrong door.
        """
        user = self._require()
        if not user:
            return
        if user["role"] != ROLE_STUDENT:
            self._err(403, "forbidden",
                      "Only a student account has a room to correct.")
            return

        college = body.get("college") or ""
        if college not in COLLEGE_BY_ID:
            self._err(400, "auth_missing_college", "Please choose your college.")
            return
        block = normalize_block(college, body.get("block"))
        if college_real_blocks(college) and not block:
            self._err(400, "auth_need_block", "Please choose a tower.")
            return
        dorm = (body.get("dorm") or "").strip()[:MAX_DORM]
        if not dorm:
            self._err(400, "auth_missing_dorm", "Please give your room number.")
            return

        updated = STORE.update_user(user["id"], {
            "college": college, "block": block, "dorm": dorm,
        })
        self._json(200, {"ok": True, "user": public_user(updated)})

    def _api_account_password(self, body, ip):
        """Change the caller's own password.

        The old one has to be supplied, so a borrowed unlocked session cannot
        take the account over; and every OTHER session is revoked, because a
        password change that leaves the old cookie usable is not a change. The
        caller's own session survives — being logged out of the tab you just
        used to change the password would be a poor reward for securing it.
        """
        user = self._require()
        if not user:
            return
        current = body.get("current_password") or ""
        password = body.get("password") or ""

        # Constant-time check, and it runs even when the field is empty so the
        # endpoint cannot be used to tell "wrong password" from "no password".
        if not verify_password(current, user.get("password", "")):
            auth_note_failure(ip, user["username"])
            self._err(400, "auth_bad_current", "That is not your current password.")
            return
        auth_clear(ip, user["username"])

        code = password_error(password)
        if code:
            self._err(400, code, "That password is not valid.")
            return
        if password == current:
            self._err(400, "auth_same_password",
                      "The new password is the same as the old one.")
            return

        STORE.update_user(user["id"], {"password": hash_password(password)})
        revoked = STORE.delete_sessions_for_user(
            user["id"], keep_token=self._session_token())
        self._json(200, {"ok": True, "revoked_sessions": revoked})

    def _handle_ai_error(self, exc):
        if exc.status in (401, 403):
            self._err(502, "key_inactive",
                      "The course model key is not active right now.")
        elif exc.status == 429:
            self._err(503, "busy", "The model is busy right now. Please try again shortly.")
        else:
            sys.stderr.write("[upstream] status=%s\n" % (exc.status,))
            self._err(502, "upstream", "The model could not be reached.")

    # ---- key enrollment --------------------------------------------------- #

    def _api_setup(self, body, ip):
        # Only an administrator may place a model key into this process.
        # A first save enrolls the key. A later save with `replace` changes
        # the provider, model, endpoint, or the key itself. An environment
        # key cannot be overwritten from the page.
        if not self._require(ROLE_ADMIN):
            return
        if self.key_source == "env":
            self._err(409, "setup_disabled_env",
                      "This server was started with a key in its environment.")
            return
        replacing = bool(body.get("replace"))
        if Handler.api_key and not replacing:
            self._err(409, "setup_already_enrolled", "A key is already enrolled.")
            return
        provider = body.get("provider") or "openrouter"
        if provider not in PROVIDERS:
            self._err(400, "setup_bad_provider", "Unknown model provider.")
            return
        raw_key = body.get("key")
        if raw_key is None:
            raw_key = ""
        if not isinstance(raw_key, str):
            self._err(400, "setup_missing_key", "Missing key.")
            return
        candidate = raw_key.strip()
        keep_existing = (replacing and Handler.key_source == "enrolled"
                         and Handler.api_key and not candidate)
        if keep_existing:
            candidate = Handler.api_key
        elif (not candidate or len(candidate) < 16 or len(candidate) > 400
              or any(ch.isspace() for ch in candidate)):
            code = "setup_missing_key" if not candidate else "setup_not_a_key"
            self._err(400, code, "That does not look like an API key.")
            return
        spec = PROVIDERS[provider]
        if provider == "custom":
            base = body.get("base_url") if isinstance(body.get("base_url"), str) else ""
            if not valid_custom_base(base):
                self._err(400, "setup_bad_base",
                          "Custom endpoints must be an https URL with no embedded credentials.")
                return
            base = base.strip().rstrip("/")
        else:
            base = spec["base"]
        model = body.get("model") if isinstance(body.get("model"), str) else ""
        model = model.strip()
        if model and not ai_agents._MODEL_RE.match(model):
            self._err(400, "setup_bad_model", "That model name is not valid.")
            return
        if not model:
            model = spec["model"] or self.model
        if not model:
            self._err(400, "setup_bad_model", "A model name is required for this provider.")
            return
        previous = (Handler.api_key, Handler.key_source, Handler.model,
                    Handler.provider, ai_agents.API_BASE, ai_agents.API_BASE_SET)
        Handler.api_key = candidate
        Handler.key_source = "enrolled"
        Handler.model = model
        Handler.provider = provider
        set_api_base(base)
        verdict = verify_key(candidate, model)
        if verdict == "auth":
            (Handler.api_key, Handler.key_source, Handler.model,
             Handler.provider, ai_agents.API_BASE, ai_agents.API_BASE_SET) = previous
            self._err(400, "setup_rejected", "The provider rejected this key (401/403).")
            return
        code = "setup_updated" if replacing else "setup_ok"
        if verdict == "unavailable":
            self._json(200, {"ok": True, "verified": False, "code": "setup_unverified",
                             "provider": provider, "model": model,
                             "message": "Key enrolled but not verified.",
                             "status": model_status()})
            return
        self._json(200, {"ok": True, "verified": True, "code": code,
                         "provider": provider, "model": model,
                         "message": "Key enrolled and verified.",
                         "status": model_status()})

    def _api_setup_reset(self, body, ip):
        """Drop a page-enrolled key. An environment key stays where it is."""
        if not self._require(ROLE_ADMIN):
            return
        if self.key_source == "env":
            self._err(409, "setup_disabled_env",
                      "This server was started with a key in its environment.")
            return
        Handler.api_key = ""
        Handler.key_source = ""
        Handler.provider = ""
        Handler.model = (os.environ.get("OPENAI_MODEL")
                         or os.environ.get("OPENROUTER_MODEL")
                         or DEFAULT_MODEL).strip()
        clear_api_base()
        self._json(200, {"ok": True, "code": "setup_reset", "status": model_status()})

    # ---- student: AI intake ---------------------------------------------- #

    def _api_intake_parse(self, body, ip):
        if not self._require(ROLE_STUDENT):
            return
        text = (body.get("text") or "").strip()[:2000]
        lang = body.get("lang") if body.get("lang") in ("zh", "en") else "zh"
        if not text:
            self._err(400, "empty_text", "Text is required.")
            return
        if is_off_topic(text):
            self._json(200, {"out_of_scope": True, "message": MSG_OUT_OF_SCOPE[lang]})
            return
        res = parse_intake(self.api_key, self.model, text, lang)
        rtype = REPAIR_BY_ID.get(res["repair_type"]) or REPAIR_BY_ID["other"]
        # The fixed labour time is deliberately NOT in this payload. It is an
        # internal scheduling constant; the student console does not display it,
        # and not sending it is the difference between "hidden" and "absent".
        self._json(200, {
            "out_of_scope": False,
            "repair_type": rtype["id"],
            "repair_label": rtype["zh"] if lang == "zh" else rtype["en"],
            "priority": res["priority"],
            "reason": res.get("reason", ""),
            "confidence": res.get("confidence", "medium"),
            "source": res.get("source", "model"),
            "needs_certified": bool(rtype.get("certified")),
            "parts": rtype["parts"] if lang == "zh" else rtype["parts_en"],
            # 追问：what the description did not say, so the student can fix it.
            "need_more_info": bool(res.get("need_more_info")),
            "follow_up": res.get("follow_up") or [],
            # 自检建议：safe steps worth trying before anybody turns up.
            "self_help": res.get("self_help") or [],
        })

    def _api_intake_advice(self, body, ip):
        if not self._require(ROLE_STUDENT):
            return
        text = (body.get("text") or "").strip()[:2000]
        lang = body.get("lang") if body.get("lang") in ("zh", "en") else "zh"
        context = body.get("context") or {}
        if not text:
            self._err(400, "empty_text", "Text is required.")
            return
        if is_off_topic(text):
            self._json(200, {"reply": MSG_OUT_OF_SCOPE[lang], "guard": "out_of_scope"})
            return
        reply = advise_student(self.api_key, self.model, text, context, lang)
        self._json(200, {"reply": reply, "guard": None})

    # ---- authenticated images + conversational intake ------------------ #

    def _api_uploads(self, body, ip):
        user = self._require(ROLE_STUDENT, ROLE_WORKER)
        if not user:
            return
        purpose = body.get("purpose") or "ticket"
        if user["role"] == ROLE_STUDENT and purpose == "completion":
            self._err(403, "forbidden", "Only technicians upload completion proof.")
            return
        if user["role"] == ROLE_WORKER and purpose != "completion":
            self._err(403, "forbidden", "Technicians may only upload completion proof.")
            return
        try:
            uploads = save_images(STORE, UPLOAD_DIR, user["id"], body.get("images"),
                                  purpose=purpose, draft_id=body.get("draft_id") or "")
        except UploadError as exc:
            self._err(400, exc.code, str(exc))
            return
        self._json(200, {"ok": True, "uploads": uploads,
                         "limits": {"count": MAX_IMAGES, "bytes_each": 5 * 1024 * 1024}})

    def _api_agent_session(self, body, ip):
        user = self._require(ROLE_STUDENT)
        if not user:
            return
        session = STORE.add_agent_session({
            "user_id": user["id"], "lang": body_lang(body), "history": [],
            "image_ids": [], "draft": None, "ready": False,
            "stage": "describe", "detected": {}, "missing": [], "self_help": [],
            "safety_warning": "", "resolved": False, "ticket_id": "",
            "route_options": [], "emergency": False, "confidence": "low",
            "kb_status": "empty", "solution": None, "next_question": None,
            "question_options": [], "state": "describe",
            "response_mode": "complete", "active_route": "",
            "agent_status": "ai",
        })
        self._json(200, {"ok": True, "lang": session["lang"],
                         "session": self._agent_session_view(session)})

    def _api_agent_message(self, body, ip):
        user = self._require(ROLE_STUDENT)
        if not user:
            return
        requested_lang = body_lang(body)
        session = STORE.agent_session(body.get("id"))
        if not session:
            self._err(404, "not_found",
                      agent_error("not_found", requested_lang), requested_lang)
            return
        if session.get("user_id") != user["id"]:
            self._err(403, "forbidden",
                      agent_error("forbidden", requested_lang), requested_lang)
            return
        lang = (body.get("lang") if body.get("lang") in ("zh", "en")
                else session.get("lang", "zh"))
        if lang not in ("zh", "en"):
            lang = "zh"
        if session.get("lang") != lang:
            session = STORE.update_agent_session(session["id"], {"lang": lang})
        if session.get("ticket_id"):
            self._err(409, "already_confirmed",
                      agent_error("already_confirmed", lang), lang)
            return
        action = body.get("action") or ""
        allowed_actions = ("", "resolved", "create_draft", "self_help",
                           "smart_repair", "manual", "save_draft")
        if action not in allowed_actions:
            self._err(400, "bad_agent_action",
                      agent_error("bad_agent_action", lang), lang)
            return
        if action == "manual":
            session = STORE.update_agent_session(session["id"], {
                "stage": "manual", "state": "manual",
                "active_route": "manual",
            })
            self._json(200, {"ok": True,
                             "reply": ("已切换为手写工单，现有草稿已带入。"
                                       if session.get("lang") == "zh" else
                                       "Manual entry is open with your saved draft."),
                             "lang": lang, **self._agent_session_view(session)})
            return
        if action == "save_draft":
            patch = body.get("draft") or {}
            if not isinstance(patch, dict):
                self._err(400, "bad_draft", agent_error("bad_draft", lang), lang)
                return
            allowed = ("title", "repair_type", "description", "priority",
                       "expected_date", "slots", "contact", "public_area",
                       "college", "block", "dorm")
            draft = dict(session.get("draft") or {})
            for key in allowed:
                if key in patch:
                    draft[key] = patch[key]
            if draft.get("repair_type") not in REPAIR_BY_ID:
                self._err(400, "bad_type", agent_error("bad_type", lang), lang)
                return
            if draft.get("priority") not in ("urgent", "high", "normal", "low"):
                self._err(400, "bad_priority", agent_error("bad_priority", lang), lang)
                return
            session = STORE.update_agent_session(session["id"], {"draft": draft})
            self._json(200, {"ok": True, "saved": True,
                             "lang": lang,
                             "session": self._agent_session_view(session)})
            return
        if action == "resolved":
            reply = ("已结束本次受理，不会创建工单。"
                     if lang == "zh"
                     else "This intake is closed. No ticket was created.")
            history = list(session.get("history") or [])
            history.append({"role": "assistant", "content": reply, "lang": lang})
            session = STORE.update_agent_session(session["id"], {
                "stage": "resolved", "resolved": True, "ready": False,
                "draft": None, "missing": [], "self_help": [],
                "state": "resolved", "history": history[-30:],
            })
            self._json(200, {
                "ok": True, "stage": "resolved", "ready": False, "draft": None,
                "reply": reply, "lang": lang,
                "detected": session.get("detected") or {}, "missing": [],
                "self_help": [], "safety_warning": "", "source": "rules",
            })
            return
        if session.get("resolved"):
            self._err(409, "session_resolved",
                      agent_error("session_resolved", lang), lang)
            return
        force_draft = action in ("create_draft", "smart_repair")
        first_turn = not (session.get("history") or []) and not action
        text = (body.get("text") or "").strip()[:2000]
        selected = body.get("option")
        if selected is not None:
            valid_options = session.get("question_options") or []
            match = next((x for x in valid_options
                          if isinstance(x, dict) and x.get("value") == selected), None)
            if not match:
                self._err(400, "bad_question_option",
                          agent_error("bad_question_option", lang), lang)
                return
            text = str(match.get("label") or selected)[:200]
        image_ids = (body.get("image_ids") if "image_ids" in body
                     else session.get("image_ids") or [])
        if not isinstance(image_ids, list) or len(image_ids) > MAX_IMAGES:
            self._err(400, "too_many_images",
                      agent_error("too_many_images", lang), lang)
            return
        if not text and not image_ids and not force_draft and action != "self_help":
            self._err(400, "empty_text", agent_error("empty_text", lang), lang)
            return
        clean_images = []
        for uid in image_ids:
            upload = STORE.upload(uid)
            if (not upload or upload.get("owner_user_id") != user["id"]
                    or upload.get("ticket_id")
                    or upload.get("purpose") not in ("agent", "ticket")):
                self._err(403, "forbidden", agent_error("forbidden", lang), lang)
                return
            clean_images.append(uid)
        history = list(session.get("history") or [])
        if text or (not force_draft and action != "self_help"):
            history.append({
                "role": "user",
                "content": text or ("[图片]" if lang == "zh" else "[Image]"),
                "image_ids": clean_images,
                "transcript": bool(body.get("transcript")),
                "lang": lang,
            })
        session = STORE.update_agent_session(session["id"], {
            "history": history[-30:],
            # The request carries the unified composer's current attachments.
            # Replacing (rather than unioning) also makes preview deletion real.
            "image_ids": list(dict.fromkeys(clean_images)),
        })
        wants_ticket = force_draft or intake_agent.student_wants_ticket(text)
        wants_advice = (not wants_ticket) and (
            action == "self_help"
            or session.get("active_route") == "self_help"
            or intake_agent.student_wants_advice(text))
        result = intake_agent.respond(self.api_key, self.model, session, user,
                                      lang, STORE, UPLOAD_DIR,
                                      force_draft=wants_ticket,
                                      advice_mode=wants_advice)
        if first_turn and not wants_ticket and not wants_advice:
            spoken = (result.get("reply") or "").strip()
            if result.get("source") != "model" or not spoken:
                spoken = ("已完成初步识别。请选择“提供维修建议”或“生成工单”。"
                          if lang == "zh" else
                          "Initial recognition is complete. Choose repair advice or create a ticket.")
            result.update({
                "reply": spoken,
                "stage": "route_choice", "state": "route_choice",
                "complete": False, "missing": [], "next_question": None,
                "question_options": [], "self_help": [], "solution": None,
                "route_options": [
                    {"id": "self_help", "enabled": True, "recommended": False},
                    {"id": "smart_repair", "enabled": True,
                     "recommended": bool(result.get("emergency"))},
                ],
                "active_route": "",
            })
        if session.get("draft") and result.get("draft") and (
                force_draft or result.get("stage") in ("advice", "no_solution")):
            merged = dict(result["draft"])
            merged.update(session["draft"])
            merged["image_ids"] = list(session.get("image_ids") or [])
            if result.get("emergency"):
                merged["priority"] = "urgent"
            result["draft"] = merged
        history = list(session.get("history") or [])
        history.append({"role": "assistant", "content": result.get("reply", ""),
                        "lang": lang})
        session = STORE.update_agent_session(session["id"], {
            "history": history[-30:], "draft": result.get("draft"), "lang": lang,
            "ready": bool(result.get("complete")),
            "stage": result.get("stage", "follow_up"),
            "detected": result.get("detected") or {},
            "missing": result.get("missing") or [],
            "self_help": result.get("self_help") or [],
            "safety_warning": result.get("safety_warning", ""),
            "route_options": result.get("route_options") or [],
            "emergency": bool(result.get("emergency")),
            "confidence": result.get("confidence", "low"),
            "kb_status": result.get("kb_status", "empty"),
            "solution": result.get("solution"),
            "next_question": result.get("next_question"),
            "question_options": result.get("question_options") or [],
            "state": result.get("state", result.get("stage", "follow_up")),
            "response_mode": result.get("response_mode", "complete"),
            "active_route": (result.get("active_route") or
                             ("self_help" if result.get("stage") == "advice"
                             else ("smart_repair" if force_draft
                                   else session.get("active_route", "")))),
        })
        self._json(200, {"ok": True,
                         "lang": lang,
                         "contract_version": result.get("contract_version", 2),
                         "reply": result.get("reply", ""),
                         "source": result.get("source"), "ready": session.get("ready"),
                         "draft": session.get("draft"),
                         "stage": session.get("stage"),
                         "detected": session.get("detected") or {},
                         "missing": session.get("missing") or [],
                         "self_help": session.get("self_help") or [],
                         "safety_warning": session.get("safety_warning", ""),
                         "route_options": session.get("route_options") or [],
                         "emergency": bool(session.get("emergency")),
                         "confidence": session.get("confidence", "low"),
                         "kb_status": session.get("kb_status", "empty"),
                         "solution": session.get("solution"),
                         "next_question": session.get("next_question"),
                         "question_options": session.get("question_options") or [],
                         "state": session.get("state"),
                         "response_mode": session.get("response_mode", "complete")})

    def _api_agent_confirm(self, body, ip):
        user = self._require(ROLE_STUDENT)
        if not user:
            return
        requested_lang = body_lang(body)
        session = STORE.agent_session(body.get("id"))
        if not session:
            self._err(404, "not_found",
                      agent_error("not_found", requested_lang), requested_lang)
            return
        if session.get("user_id") != user["id"]:
            self._err(403, "forbidden",
                      agent_error("forbidden", requested_lang), requested_lang)
            return
        lang = (body.get("lang") if body.get("lang") in ("zh", "en")
                else session.get("lang", "zh"))
        if lang not in ("zh", "en"):
            lang = "zh"
        if session.get("lang") != lang:
            session = STORE.update_agent_session(session["id"], {"lang": lang})
        if session.get("ticket_id"):
            self._json(200, {"ok": True, "idempotent": True,
                             "lang": lang,
                             "ticket": {"id": session.get("ticket_id")}})
            return
        if (not session.get("draft") or
                (not session.get("ready") and session.get("stage") != "manual")):
            self._err(409, "draft_not_ready",
                      agent_error("draft_not_ready", lang), lang)
            return
        draft = session["draft"]
        create_body = {
            "lang": lang,
            "repair_type": body.get("repair_type") or draft.get("repair_type"),
            "priority": body.get("priority") or draft.get("priority"),
            "description": (body.get("description")
                            if body.get("description") is not None
                            else draft.get("description")),
            "date": body.get("date"), "slots": body.get("slots"),
            # Attachments come from the server-side session, not editable IDs
            # supplied during confirmation.
            "image_ids": session.get("image_ids") or [],
            "student_name": body.get("student_name"),
            "contact": body.get("contact"),
            "title": body.get("title") or draft.get("title", ""),
            "public_area": bool(body.get("public_area")),
            "college": body.get("college"), "block": body.get("block"),
            "dorm": body.get("dorm"),
        }
        self._create_ticket_for_user(user, create_body, agent_session_id=session["id"])

    def _api_agent_event(self, body, ip):
        user = self._require(ROLE_STUDENT)
        if not user:
            return
        session = STORE.agent_session(body.get("id"))
        if not session or session.get("user_id") != user["id"]:
            self._err(403, "forbidden", "That agent session is not yours.")
            return
        event = body.get("event")
        allowed = ("intake_started", "route_selected", "question_answered",
                   "draft_saved", "draft_restored", "confirm_started",
                   "confirm_succeeded", "network_error", "upload_failed",
                   "resolved_without_ticket")
        if event not in allowed:
            self._err(400, "bad_event", "Unsupported event.")
            return
        STORE.add_agent_event({
            "session_id": session["id"], "event": event,
            "state": str(body.get("state") or session.get("state") or "")[:30],
            "route": str(body.get("route") or session.get("active_route") or "")[:30],
            "emergency": bool(session.get("emergency")),
            "lang": session.get("lang", "zh"),
        })
        self._json(200, {"ok": True, "lang": session.get("lang", "zh")})

    # ---- student: create ticket ------------------------------------------ #

    def _api_create_ticket(self, body, ip):
        user = self._require(ROLE_STUDENT)
        if not user:
            return
        self._create_ticket_for_user(user, body)

    def _create_ticket_for_user(self, user, body, agent_session_id=""):
        lang = body.get("lang") if body.get("lang") in ("zh", "en") else "zh"

        # ------------------------------------------------------------------ #
        # 地址 / The address
        #
        # This used to be read straight out of the request body, which meant a
        # student could file a request for any room in any college and the
        # server would happily stamp it into their records — one account ended
        # up "living" in nine colleges. The address is now taken from the
        # account; the body is ignored unless the student explicitly says this
        # is a public area (a corridor light, the laundry room), which is the
        # only case where a second address is legitimate.
        # ------------------------------------------------------------------ #
        public = bool(body.get("public_area"))
        if public:
            college = body.get("college")
            if college not in COLLEGE_BY_ID:
                self._err(400, "bad_college", "Unknown college.")
                return
            block = normalize_block(college, body.get("block"))
            dorm = (body.get("dorm") or "").strip()[:40]
            if not dorm:
                self._err(400, "bad_dorm",
                          "Please describe where the public area is.")
                return
        else:
            college = user.get("college", "")
            block = user.get("block", "")
            dorm = user.get("dorm", "")
            if not college or not dorm:
                self._err(409, "account_no_address",
                          "Your account has no room on file yet.")
                return

        repair_type = body.get("repair_type")
        if repair_type not in REPAIR_BY_ID:
            repair_type = "other"
        day = body.get("date")
        try:
            date.fromisoformat(day)
        except Exception:
            self._err(400, "bad_date", "Invalid date.")
            return
        slots = body.get("slots")
        if not isinstance(slots, list) or not slots:
            self._err(400, "bad_slots", "At least one availability window is required.")
            return
        clean_slots = []
        for pair in slots:
            try:
                s, e = int(pair[0]), int(pair[1])
            except Exception:
                continue
            if 0 <= s <= e <= 20:
                clean_slots.append([s, e])
        if not clean_slots:
            self._err(400, "bad_slots", "No valid availability window was provided.")
            return

        priority = body.get("priority", "normal")
        if priority not in PRIORITY_LEVELS:
            priority = "normal"

        image_ids = body.get("image_ids") or []
        if not isinstance(image_ids, list) or len(image_ids) > MAX_IMAGES:
            self._err(400, "too_many_images", "At most five images are allowed.")
            return
        clean_images = []
        for uid in image_ids:
            upload = STORE.upload(uid)
            if (not upload or upload.get("owner_user_id") != user["id"]
                    or upload.get("ticket_id")):
                self._err(403, "forbidden", "An image is unavailable.")
                return
            clean_images.append(uid)

        ticket = {
            # Ownership is stamped from the session, never from the body: that
            # is what makes "only your own tickets" enforceable later.
            "user_id": user["id"],
            "user_username": user["username"],
            "college": college,
            "block": block,
            "dorm": dorm,
            #: True only for a shared area, i.e. an address that is not the
            #: student's own room. The console labels these so a dorm resident
            #: can tell their own four walls apart from the corridor.
            "public": public,
            "repair_type": repair_type,
            "priority": priority,
            "date": day,
            "slots": clean_slots,
            "description": (body.get("description") or "").strip()[:1000],
            "student_name": ((body.get("student_name") or "").strip()[:60]
                             or default_student_name(user, lang)),
            "contact": ((body.get("contact") or "").strip()[:80]
                        or user.get("phone", "")),
            "lang": lang,
            "ai_reason": (body.get("ai_reason") or "").strip()[:300],
            "ai_source": (body.get("ai_source") or "").strip()[:20],
            "title": (body.get("title") or "").strip()[:80],
            "image_ids": clean_images,
            "completion_image_ids": [],
            "agent_session_id": agent_session_id or "",
        }
        saved = STORE.add_ticket(ticket)
        for uid in clean_images:
            STORE.update_upload(uid, {"ticket_id": saved["id"], "draft_id": ""})
        if agent_session_id:
            STORE.update_agent_session(agent_session_id, {"ticket_id": saved["id"]})
        self._json(200, {"ok": True, "lang": lang, "ticket": ticket_view(
            saved, user, lang, day_context(day, lang))})

    def _api_ticket_status(self, body, ip):
        """Advance a ticket's life cycle. Each role may only do its own half.

        Both halves are ownership-checked against the session: a student can
        only touch a ticket they filed, a technician only one that the engine
        assigned to them. The ticket id from the browser is never trusted on
        its own.
        """
        user = self._require(ROLE_STUDENT, ROLE_WORKER)
        if not user:
            return
        tid = body.get("id")
        status = body.get("status")
        if status not in STATUS_ALLOWED[user["role"]]:
            self._err(400, "bad_status", "That status change is not allowed.")
            return
        t = STORE.ticket(tid)
        if not t:
            self._err(404, "not_found", "Ticket not found.")
            return
        if user["role"] == ROLE_STUDENT:
            if t.get("user_id") != user["id"]:
                self._err(403, "forbidden", "That ticket is not yours.")
                return
        else:
            mine = acting_worker_id(user)
            owner = assignment_map(t.get("date", "")).get(tid)
            if owner != mine:
                self._err(403, "forbidden", "That job is not on your schedule.")
                return

        current = t.get("status", "pending")
        allowed_from = {
            "accepted": ("pending",),
            "done": ("accepted",),
            "confirmed": ("done",),
            "pending": ("done", "confirmed", "pending"),
        }
        if status != current and current not in allowed_from.get(status, ()):
            self._err(409, "bad_status_transition",
                      "That status does not follow the ticket workflow.")
            return

        patch = {"status": status}
        now = datetime.now().isoformat(timespec="seconds")
        if status == "accepted":
            # Record who picked it up, so the assignment stops being recomputed
            # and the student can be told who is coming.
            patch["assigned_worker"] = acting_worker_id(user)
            patch["accepted_at"] = now
        elif status == "done":
            image_ids = body.get("completion_image_ids") or []
            if not isinstance(image_ids, list) or not image_ids:
                self._err(400, "completion_images_required",
                          "At least one completion photo is required.")
                return
            if len(image_ids) > MAX_IMAGES:
                self._err(400, "too_many_images", "At most five images are allowed.")
                return
            clean_images = []
            for uid in image_ids:
                upload = STORE.upload(uid)
                if (not upload or upload.get("owner_user_id") != user["id"]
                        or upload.get("purpose") != "completion"
                        or (upload.get("ticket_id") and upload.get("ticket_id") != tid)):
                    self._err(403, "forbidden", "A completion image is unavailable.")
                    return
                clean_images.append(uid)
            patch["done_at"] = now
            patch["completed_at"] = now
            patch["completed_by"] = acting_worker_id(user)
            patch["completion_image_ids"] = clean_images
            for uid in clean_images:
                STORE.update_upload(uid, {"ticket_id": tid})
        elif status == "confirmed":
            patch["confirmed_at"] = now
        elif status == "pending":
            # Re-opened: the student says it is not fixed, so the job goes back
            # into the pool and the appointment is recomputed. Any answer the
            # technician had given to "can you fit it in?" is stale by now.
            patch["reopened_at"] = now
            patch["done_at"] = None
            patch["confirmed_at"] = None
            patch["off_route"] = False
            patch["reschedule_requested"] = False
            patch["reschedule_note"] = ""
            patch["suggested_date"] = ""
        updated = STORE.update_ticket(tid, patch)
        self._json(200, {"ok": True, "ticket": ticket_view(
            updated, user, body_lang(body), day_context(updated.get("date", ""), body_lang(body)))})

    # ---- 排不进当天的单 / jobs the engine could not fit into the day ------ #

    def _api_ticket_offroute(self, body, ip):
        """A technician's call on a job the engine could not place today.

        The engine is deterministic and does not bend: if the declared windows
        and the shift cannot hold a job, it stays off the route and no amount of
        clicking moves it. But the engine is not standing in the building. Only
        the technician can decide whether it is worth squeezing in on the way
        past, or whether the student should rebook — so this records that
        decision, and recording it is what changes what the student is told.
        """
        user = self._require(ROLE_WORKER)
        if not user:
            return
        tid = body.get("id")
        action = body.get("action")
        if action not in ("take", "reschedule"):
            self._err(400, "bad_action", "Unknown action.")
            return
        t = STORE.ticket(tid)
        if not t:
            self._err(404, "not_found", "Ticket not found.")
            return
        mine = acting_worker_id(user)
        if assignment_map(t.get("date", "")).get(tid) != mine:
            self._err(403, "forbidden", "That job is not on your round.")
            return
        # This endpoint answers exactly one question — "this did not fit, what
        # now?" — so it only applies to jobs the engine actually failed to
        # place. Without this check a technician could mark a perfectly
        # scheduled job as off-route and the timeline would stop meaning
        # anything.
        _worker, _jobs, unscheduled, _stats = build_plan(
            t.get("date", ""), mine, body_lang(body))
        if tid not in {u["id"] for u, _reason in unscheduled}:
            self._err(409, "on_route",
                      "That job is already on the route for that day.")
            return

        now = datetime.now().isoformat(timespec="seconds")
        if action == "take":
            # "I'll do it on the way past." It becomes an accepted job — so the
            # student learns who is coming — but it stays OFF the optimised
            # route, because the engine never placed it and putting it on the
            # timeline would make the timeline a lie.
            patch = {"status": "accepted", "assigned_worker": mine,
                     "accepted_at": now, "off_route": True, "off_route_at": now,
                     "reschedule_requested": False, "reschedule_note": "",
                     "suggested_date": ""}
        else:
            suggested = (body.get("suggested_date") or "").strip()
            if suggested:
                try:
                    date.fromisoformat(suggested)
                except Exception:
                    self._err(400, "bad_date", "Invalid date.")
                    return
            patch = {"off_route": False, "reschedule_requested": True,
                     "reschedule_note": (body.get("note") or "").strip()[:200],
                     "suggested_date": suggested, "reschedule_at": now}
        updated = STORE.update_ticket(tid, patch)
        lang = body_lang(body)
        self._json(200, {"ok": True, "ticket": ticket_view(
            updated, user, lang, day_context(updated.get("date", ""), lang))})

    def _api_ticket_reschedule(self, body, ip):
        """The student's answer to "shall we move this to another day?".

        Accepting keeps the windows they already declared — a student's free
        hours belong to their timetable, not to one particular date — and moves
        the ticket onto the suggested day, where the engine gets another go at
        placing it.
        """
        user = self._require(ROLE_STUDENT)
        if not user:
            return
        tid = body.get("id")
        t = STORE.ticket(tid)
        if not t:
            self._err(404, "not_found", "Ticket not found.")
            return
        if t.get("user_id") != user["id"]:
            self._err(403, "forbidden", "That ticket is not yours.")
            return
        if not t.get("reschedule_requested"):
            self._err(409, "no_suggestion",
                      "There is no suggested new time on that ticket.")
            return

        now = datetime.now().isoformat(timespec="seconds")
        if body.get("accept"):
            new_day = t.get("suggested_date") or ""
            try:
                date.fromisoformat(new_day)
            except Exception:
                self._err(400, "bad_date", "Invalid suggested date.")
                return
            patch = {"date": new_day, "reschedule_requested": False,
                     "reschedule_note": "", "suggested_date": "",
                     "rescheduled_at": now, "rescheduled_from": t.get("date", ""),
                     # Cleared so the engine re-derives the assignment for the
                     # new day rather than inheriting yesterday's answer.
                     "assigned_worker": ""}
        else:
            # Declined: the ticket stays where it was, still unplaced, and the
            # suggestion is cleared so the record stops claiming something the
            # student has already answered.
            patch = {"reschedule_requested": False, "reschedule_note": "",
                     "suggested_date": "", "reschedule_declined_at": now}
        updated = STORE.update_ticket(tid, patch)
        lang = body_lang(body)
        self._json(200, {"ok": True, "ticket": ticket_view(
            updated, user, lang, day_context(updated.get("date", ""), lang))})

    def _api_ticket_feedback(self, body, ip):
        """The student's verdict on a finished job — the point of the whole app."""
        user = self._require(ROLE_STUDENT)
        if not user:
            return
        tid = body.get("id")
        t = STORE.ticket(tid)
        if not t:
            self._err(404, "not_found", "Ticket not found.")
            return
        if t.get("user_id") != user["id"]:
            self._err(403, "forbidden", "That ticket is not yours.")
            return
        try:
            rating = int(body.get("rating") or 0)
        except Exception:
            rating = 0
        if rating < 0 or rating > 5:
            self._err(400, "bad_rating", "Rating must be between 0 and 5.")
            return
        patch = {
            "rating": rating,
            "feedback": (body.get("feedback") or "").strip()[:500],
            "feedback_at": datetime.now().isoformat(timespec="seconds")
            if rating or (body.get("feedback") or "").strip() else None,
        }
        # Leaving a rating is how a student closes the loop; a 1-2 star verdict
        # also puts the ticket back in the queue rather than quietly filing it.
        if rating and rating <= 2 and t.get("status") == "done":
            patch["status"] = "pending"
            patch["reopened_at"] = patch["feedback_at"]
        updated = STORE.update_ticket(tid, patch)
        lang = body_lang(body)
        self._json(200, {"ok": True, "ticket": ticket_view(
            updated, user, lang, day_context(updated.get("date", ""), lang))})

    def _api_ticket_delete(self, body, ip):
        user = self._require(ROLE_STUDENT)
        if not user:
            return
        tid = body.get("id")
        t = STORE.ticket(tid)
        if not t:
            self._err(404, "not_found", "Ticket not found.")
            return
        if t.get("user_id") != user["id"]:
            self._err(403, "forbidden", "That ticket is not yours.")
            return
        if t.get("status") not in ("pending",):
            # Once a technician is on the way, withdrawing it silently would
            # waste a trip — ask them to contact the office instead.
            self._err(409, "delete_not_allowed",
                      "A job already under way cannot be deleted.")
            return
        self._json(200, {"ok": STORE.delete_ticket(tid)})

    # ---- worker: optimize + AI dispatch advice ---------------------------- #

    def _api_optimize(self, body, ip):
        user = self._require(ROLE_WORKER)
        if not user:
            return
        lang = body_lang(body)
        day = body.get("date") or date.today().isoformat()
        try:
            date.fromisoformat(day)
        except Exception:
            self._err(400, "bad_date", "Invalid date.")
            return
        # No `worker` parameter, by design: your session says whose day it is.
        payload = plan_payload(day, acting_worker_id(user), lang)
        payload["ok"] = True
        self._json(200, payload)

    def _api_dispatch_advice(self, body, ip):
        user = self._require(ROLE_WORKER)
        if not user:
            return
        lang = body_lang(body)
        day = body.get("date") or date.today().isoformat()
        mode = body.get("mode", "dispatch")     # "dispatch" | "rationale"
        worker_id = acting_worker_id(user)
        worker, jobs, unscheduled, stats = build_plan(day, worker_id, lang)
        if mode == "rationale":
            text = summarize_plan(self.api_key, self.model, day, jobs, stats, worker, lang)
            if not text:
                text = _local_rationale(day, jobs, stats, lang)
        else:
            text = advise_dispatch(self.api_key, self.model, day, jobs, stats, worker, lang)
        self._json(200, {
            "reply": text,
            "ai_enabled": bool(self.api_key),
            "stats": stats,
        })

    # ---- administrator: the account desk ---------------------------------- #
    #
    # These are the three things that genuinely needed a person with authority
    # and no stake in any one ticket:
    #
    #   * a room typed wrong at sign-up (the address is what every future
    #     ticket is filed against, so it must be correctable by somebody other
    #     than the student — a student cannot edit their own room by design);
    #   * a forgotten password;
    #   * a technician account, which this system never let people claim.

    def _admin_lang(self, body):
        return body_lang(body)

    def _api_admin_user_update(self, body, ip):
        """Correct an account: display name, and for a student the room."""
        if not self._require(ROLE_ADMIN):
            return
        lang = self._admin_lang(body)
        target = STORE.user(body.get("id"))
        if not target:
            self._err(404, "not_found", "Account not found.")
            return
        patch = {}
        if body.get("name") is not None:
            name = clean_name(body.get("name"))
            if not name:
                self._err(400, "auth_missing_name", "Please give a name.")
                return
            patch["name"] = name
        # A room only means something for a student: a technician has no dorm
        # and an administrator is not a resident.
        if target.get("role") == ROLE_STUDENT:
            if body.get("phone") is not None:
                phone, code = normalize_student_phone(body.get("phone"), required=False)
                if code:
                    self._err(400, code, "Please give a valid mainland China mobile number.")
                    return
                patch["phone"] = phone
            if body.get("college") is not None:
                college = body.get("college") or ""
                if college not in COLLEGE_BY_ID:
                    self._err(400, "auth_missing_college", "Unknown college.")
                    return
                block = normalize_block(college, body.get("block"))
                if college_real_blocks(college) and not block:
                    self._err(400, "auth_need_block", "Please choose a tower.")
                    return
                patch["college"] = college
                patch["block"] = block
            elif body.get("block") is not None:
                patch["block"] = normalize_block(target.get("college", ""),
                                                 body.get("block"))
            if body.get("dorm") is not None:
                dorm = (body.get("dorm") or "").strip()[:MAX_DORM]
                if not dorm:
                    self._err(400, "auth_missing_dorm", "Please give a room number.")
                    return
                patch["dorm"] = dorm
        if not patch:
            self._err(400, "nothing_to_update", "There is nothing to update.")
            return
        updated = STORE.update_user(target["id"], patch)
        self._json(200, {"ok": True, "user": admin_user_view(updated, lang)})

    def _api_admin_user_password(self, body, ip):
        """Reset a password, and revoke every session that account still holds.

        A reset that leaves the old session alive is not a reset, so the two
        happen together: the account is signed out everywhere, then can sign
        back in with the new password.
        """
        if not self._require(ROLE_ADMIN):
            return
        target = STORE.user(body.get("id"))
        if not target:
            self._err(404, "not_found", "Account not found.")
            return
        password = body.get("password") or ""
        code = password_error(password)
        if code:
            self._err(400, code, "That password is not valid.")
            return
        STORE.update_user(target["id"], {"password": hash_password(password)})
        revoked = STORE.delete_sessions_for_user(target["id"])
        self._json(200, {"ok": True, "revoked_sessions": revoked})

    def _api_admin_user_create(self, body, ip):
        """Issue a student account — the same fields sign-up asks for.

        Technicians do NOT come through here: their account has to be paired
        with a roster record (`_api_admin_worker_create`), or the engine would
        have nobody to assign their jobs to.
        """
        if not self._require(ROLE_ADMIN):
            return
        lang = self._admin_lang(body)
        username = clean_username(body.get("username"))
        password = body.get("password") or ""
        name = clean_name(body.get("name"))
        college = body.get("college") or ""
        phone, phone_code = normalize_student_phone(body.get("phone"), required=True)

        code = username_error(username) or password_error(password)
        if code:
            self._err(400, code, "That account or password is not valid.")
            return
        if not name:
            self._err(400, "auth_missing_name", "Please give a name.")
            return
        if phone_code:
            self._err(400, phone_code, "Please give a valid mainland China mobile number.")
            return
        if STORE.user_by_username(username):
            self._err(409, "auth_username_taken", "That account already exists.")
            return
        if college not in COLLEGE_BY_ID:
            self._err(400, "auth_missing_college", "Please choose a college.")
            return
        block = normalize_block(college, body.get("block"))
        if college_real_blocks(college) and not block:
            self._err(400, "auth_need_block", "Please choose a tower.")
            return
        dorm = (body.get("dorm") or "").strip()[:MAX_DORM]
        if not dorm:
            self._err(400, "auth_missing_dorm", "Please give a room number.")
            return

        created = STORE.add_user({
            "role": ROLE_STUDENT,
            "username": username,
            "name": name,
            "name_en": "",
            "phone": phone,
            "college": college,
            "block": block,
            "dorm": dorm,
            "worker_id": "",
            "worker_ref": "",
            "password": hash_password(password),
        })
        self._json(200, {"ok": True, "created": True,
                         "user": admin_user_view(created, lang)})

    def _api_admin_worker_create(self, body, ip):
        """Issue a technician: a roster record AND the account bound to it.

        Both halves are needed and they must agree: `assign_day()` hands work
        out by the roster record, and the technician console resolves "my day"
        from the account's `worker_ref`. Creating only one of them would leave
        an account that signs in to an empty console, or a roster entry nobody
        can log in as.
        """
        if not self._require(ROLE_ADMIN):
            return
        lang = self._admin_lang(body)
        worker_id = clean_username(body.get("worker_id"))
        password = body.get("password") or ""
        name = clean_name(body.get("name"))

        code = username_error(worker_id) or password_error(password)
        if code:
            self._err(400, code, "That staff number or password is not valid.")
            return
        if not name:
            self._err(400, "auth_missing_name", "Please give a name.")
            return
        if STORE.user_by_username(worker_id):
            self._err(409, "auth_username_taken", "That account already exists.")
            return
        # Trade coverage is what the dispatcher matches on, so a technician
        # with no specialties could never be given a job.
        specs = body.get("specialties")
        if not isinstance(specs, list):
            specs = []
        specs = [s for s in specs if s in REPAIR_BY_ID]
        if not specs:
            self._err(400, "need_specialty", "Pick at least one trade the technician covers.")
            return

        record_id = "W-" + worker_id
        if STORE.worker(record_id):
            self._err(409, "worker_taken", "A technician record with that number already exists.")
            return
        record = STORE.add_worker({
            "id": record_id,
            "name": name,
            "name_en": (body.get("name_en") or "").strip()[:MAX_NAME],
            "worker_id": worker_id,
            "trade_zh": (body.get("trade_zh") or "").strip()[:MAX_TRADE],
            "trade_en": (body.get("trade_en") or "").strip()[:MAX_TRADE],
            "specialties": specs,
            "certified": bool(body.get("certified")),
            "phone": (body.get("phone") or "").strip()[:MAX_PHONE],
        })
        account = STORE.add_user({
            "role": ROLE_WORKER,
            "username": worker_id,
            "name": name,
            "name_en": record["name_en"],
            "college": "",
            "block": "",
            "dorm": "",
            "worker_id": worker_id,
            "worker_ref": record["id"],
            "password": hash_password(password),
        })
        self._json(200, {"ok": True, "created": True,
                         "worker": admin_worker_view(record, lang),
                         "user": admin_user_view(account, lang)})

    @staticmethod
    def _admin_clean_slots(value):
        if not isinstance(value, list) or not value:
            return None
        clean = []
        for pair in value:
            if not isinstance(pair, (list, tuple)) or len(pair) != 2:
                return None
            try:
                start, end = int(pair[0]), int(pair[1])
            except (TypeError, ValueError):
                return None
            if not (0 <= start <= end <= 20):
                return None
            clean.append([start, end])
        return clean

    def _admin_ticket_business_patch(self, body, base=None, creating=False):
        """Validate editable business fields without accepting ownership metadata."""
        current = base or {}
        patch = {}
        if creating or "title" in body:
            title = (body.get("title") or "").strip()[:80]
            if not title:
                return None, ("bad_fields", "A title is required.")
            patch["title"] = title
        if creating or "repair_type" in body:
            repair_type = body.get("repair_type")
            if repair_type not in REPAIR_BY_ID:
                return None, ("bad_fields", "Unknown repair category.")
            patch["repair_type"] = repair_type
        if creating or "priority" in body:
            priority = body.get("priority")
            if priority not in PRIORITY_LEVELS:
                return None, ("bad_fields", "Unknown priority.")
            patch["priority"] = priority
        if creating or "date" in body:
            day = body.get("date")
            try:
                date.fromisoformat(day)
            except (TypeError, ValueError):
                return None, ("bad_fields", "Invalid date.")
            patch["date"] = day
        if creating or "slots" in body:
            slots = self._admin_clean_slots(body.get("slots"))
            if slots is None:
                return None, ("bad_fields", "Invalid availability windows.")
            patch["slots"] = slots
        for key, maximum in (("description", 1000), ("student_name", 60),
                             ("contact", 80)):
            if key in body:
                patch[key] = (body.get(key) or "").strip()[:maximum]

        address_keys = {"college", "block", "dorm", "public_area", "public"}
        if creating or any(key in body for key in address_keys):
            college = body.get("college", current.get("college", ""))
            if college not in COLLEGE_BY_ID:
                return None, ("bad_fields", "Unknown college.")
            block = normalize_block(college, body.get("block", current.get("block", "")))
            if college_real_blocks(college) and not block:
                return None, ("bad_fields", "A tower is required for this college.")
            dorm = (body.get("dorm", current.get("dorm", "")) or "").strip()[:MAX_DORM]
            if not dorm:
                return None, ("bad_fields", "A repair location is required.")
            patch.update({
                "college": college,
                "block": block,
                "dorm": dorm,
                "public": bool(body.get(
                    "public_area", body.get("public", current.get("public", False)))),
            })
        return patch, None

    @staticmethod
    def _admin_audit(actor, action, ticket_id, changed_fields, reason=""):
        event = {
            "actor_admin_id": actor.get("id", ""),
            "action": action,
            "ticket_id": ticket_id,
            "changed_fields": sorted(set(changed_fields)),
        }
        if reason:
            event["override_reason"] = reason[:300]
        return STORE.add_admin_audit(event)

    def _api_admin_ticket_create(self, body, ip):
        admin = self._require(ROLE_ADMIN)
        if not admin:
            return
        allowed = {
            "owner_user_id", "title", "repair_type", "priority", "date", "slots",
            "description", "student_name", "contact", "college", "block", "dorm",
            "public_area", "public", "lang", "client_request_id",
        }
        if set(body) - allowed:
            self._err(400, "bad_fields", "The request contains fields that cannot be set.")
            return
        owner = STORE.user(body.get("owner_user_id"))
        if not owner or owner.get("role") != ROLE_STUDENT:
            self._err(400, "bad_owner", "Choose an existing student account.")
            return
        prepared = dict(body)
        prepared.setdefault("college", owner.get("college", ""))
        prepared.setdefault("block", owner.get("block", ""))
        prepared.setdefault("dorm", owner.get("dorm", ""))
        prepared.setdefault("student_name", default_student_name(owner, body_lang(body)))
        prepared.setdefault("contact", owner.get("phone", ""))
        patch, error = self._admin_ticket_business_patch(prepared, creating=True)
        if error:
            self._err(400, error[0], error[1])
            return
        request_id = (body.get("client_request_id") or "").strip()[:100]
        if body.get("client_request_id") is not None and not request_id:
            self._err(400, "bad_fields", "client_request_id cannot be empty.")
            return
        ticket = {
            **patch,
            "user_id": owner["id"],
            "user_username": owner.get("username", ""),
            "student_name": patch.get("student_name") or default_student_name(
                owner, body_lang(body)),
            "contact": patch.get("contact") or owner.get("phone", ""),
            "lang": body_lang(body),
            "status": "pending",
            "assigned_worker": "",
            "image_ids": [],
            "completion_image_ids": [],
            "ai_reason": "",
            "ai_source": "",
            "agent_session_id": "",
        }
        fingerprint_body = {key: body.get(key) for key in sorted(allowed)
                            if key not in ("client_request_id", "lang")}
        fingerprint = hashlib.sha256(json.dumps(
            fingerprint_body, ensure_ascii=False, sort_keys=True,
            separators=(",", ":")).encode("utf-8")).hexdigest()
        outcome, saved = STORE.add_ticket_idempotent(
            ticket, request_id, admin["id"], fingerprint)
        if outcome == "conflict":
            self._err(409, "idempotency_conflict",
                      "That request id was already used for different ticket data.")
            return
        if outcome == "created":
            self._admin_audit(
                admin, "create", saved["id"], list(patch.keys()) + ["owner_user_id"])
        lang = body_lang(body)
        self._json(200, {
            "ok": True,
            "created": outcome == "created",
            "idempotent_replay": outcome == "existing",
            "ticket": ticket_view(saved, _ADMIN_PRINCIPAL, lang,
                                  day_context(saved.get("date", ""), lang)),
        })

    def _api_admin_ticket_update(self, body, ip):
        admin = self._require(ROLE_ADMIN)
        if not admin:
            return
        allowed = {
            "id", "expected_updated_at", "expected_version", "title", "repair_type",
            "priority", "date", "slots", "description", "student_name", "contact",
            "college", "block", "dorm", "public_area", "public", "status",
            "assigned_worker", "completion_image_ids", "override_completion_evidence",
            "override_reason", "lang",
        }
        if set(body) - allowed or "owner_user_id" in body:
            self._err(400, "bad_fields", "The request contains fields that cannot be edited.")
            return
        ticket = STORE.ticket(body.get("id"))
        if not ticket:
            self._err(404, "not_found", "Ticket not found.")
            return
        try:
            expected_version = (int(body["expected_version"])
                                if body.get("expected_version") is not None else None)
        except (TypeError, ValueError):
            self._err(400, "bad_fields", "expected_version must be an integer.")
            return
        expected_updated_at = body.get("expected_updated_at")
        if expected_updated_at is not None and not isinstance(expected_updated_at, str):
            self._err(400, "bad_fields", "expected_updated_at must be a string.")
            return

        patch, error = self._admin_ticket_business_patch(body, ticket, creating=False)
        if error:
            self._err(400, error[0], error[1])
            return
        changed = set(patch)
        worker = ticket.get("assigned_worker", "")
        if "assigned_worker" in body:
            worker = (body.get("assigned_worker") or "").strip()
            if worker and not STORE.worker(worker):
                self._err(400, "bad_worker", "Choose an existing technician.")
                return
            patch["assigned_worker"] = worker
            changed.add("assigned_worker")

        status = body.get("status", ticket.get("status", "pending"))
        if "status" in body:
            if status not in STATUS_FLOW:
                self._err(400, "bad_status", "Unknown ticket status.")
                return
            if status in ("accepted", "done") and not worker:
                self._err(400, "bad_worker", "Accepted and completed tickets need a technician.")
                return
            patch["status"] = status
            changed.add("status")

        evidence = ticket.get("completion_image_ids") or []
        if "completion_image_ids" in body:
            supplied = body.get("completion_image_ids")
            if not isinstance(supplied, list) or len(supplied) > MAX_IMAGES:
                self._err(400, "bad_fields", "Invalid completion evidence.")
                return
            evidence = []
            for upload_id in supplied:
                upload = STORE.upload(upload_id)
                if (not upload or upload.get("purpose") != "completion"
                        or upload.get("ticket_id") not in ("", ticket["id"])):
                    self._err(400, "bad_fields", "Invalid completion evidence.")
                    return
                evidence.append(upload_id)
            patch["completion_image_ids"] = evidence
            changed.add("completion_image_ids")

        override = bool(body.get("override_completion_evidence"))
        reason = (body.get("override_reason") or "").strip()
        if override and not reason:
            self._err(400, "override_reason_required",
                      "An override reason is required.")
            return
        if (status == "done" and not evidence and not override
                and ("status" in body or "completion_image_ids" in body)):
            self._err(400, "evidence_required",
                      "Completion evidence is required unless an override is recorded.")
            return

        now = datetime.now().isoformat(timespec="seconds")
        current_status = ticket.get("status", "pending")
        if "status" in body:
            if status == "pending":
                patch.update({
                    "accepted_at": None, "done_at": None, "completed_at": None,
                    "completed_by": "", "confirmed_at": None,
                })
            elif status == "accepted":
                if current_status != "accepted":
                    patch["accepted_at"] = now
                patch.update({
                    "done_at": None, "completed_at": None, "completed_by": "",
                    "confirmed_at": None,
                })
            elif status == "done":
                if current_status != "done":
                    patch["done_at"] = now
                    patch["completed_at"] = now
                patch["completed_by"] = worker
                patch["confirmed_at"] = None
            elif status == "confirmed":
                if current_status != "confirmed":
                    patch["confirmed_at"] = now
            changed.update(key for key in patch if key.endswith("_at") or key == "completed_by")
        elif worker and current_status == "done" and "assigned_worker" in body:
            patch["completed_by"] = worker
            changed.add("completed_by")

        operational = changed - {"accepted_at", "done_at", "completed_at",
                                 "completed_by", "confirmed_at"}
        if not operational:
            self._err(400, "bad_fields", "There is nothing to update.")
            return
        outcome, updated = STORE.update_ticket_if_current(
            ticket["id"], patch, expected_updated_at, expected_version)
        if outcome == "conflict":
            self._err(409, "edit_conflict",
                      "The ticket changed after this form was opened.")
            return
        if outcome == "missing":
            self._err(404, "not_found", "Ticket not found.")
            return
        for upload_id in evidence:
            STORE.update_upload(upload_id, {"ticket_id": ticket["id"]})
        self._admin_audit(admin, "update", ticket["id"], changed,
                          reason if override else "")
        lang = body_lang(body)
        self._json(200, {"ok": True, "ticket": ticket_view(
            updated, _ADMIN_PRINCIPAL, lang,
            day_context(updated.get("date", ""), lang))})

    def _api_admin_ticket_delete(self, body, ip):
        """Remove any ticket. The operator's remedy for a duplicate or a test."""
        admin = self._require(ROLE_ADMIN)
        if not admin:
            return
        if set(body) - {"id", "lang"}:
            self._err(400, "bad_fields", "The request contains fields that cannot be set.")
            return
        tid = body.get("id")
        if not STORE.ticket(tid):
            self._err(404, "not_found", "Ticket not found.")
            return
        deleted = STORE.delete_ticket(tid)
        if deleted:
            self._admin_audit(admin, "delete", tid, [])
        self._json(200, {"ok": deleted})


def _local_rationale(day, jobs, stats, lang):
    """Deterministic explanation of the plan, used when no model is available."""
    if not jobs:
        return ("今日无工单可排。" if lang == "zh" else "No jobs to schedule today.")
    colleges = []
    for j in jobs:
        if j["college_label"] not in colleges:
            colleges.append(j["college_label"])
    if lang == "zh":
        return (
            "**排班依据（系统确定性算法）**\n\n"
            "1. **优先级**：先排紧急单，再排较急、一般、不急；同级按报修先后顺序。\n"
            "2. **固定工时**：每一项维修占用其类别固定时长，不做估算也不做压缩。\n"
            "3. **路程规则**：书院之间往返计 15 分钟，同一书院内计 5 分钟；"
            "第 1 单从维修间出发，按跨书院 15 分钟计。\n"
            "4. **空闲时间**：所有作业只落在学生声明的空闲时段内，且不跨越 "
            "12:00-13:30 午休。\n\n"
            "今天共排 %d 单，覆盖 %s；维修工时 %d 分钟，路程 %d 分钟，"
            "首单 %s 开始，末单 %s 结束。"
            % (stats["jobs"], "、".join(colleges), stats["work_minutes"],
               stats["travel_minutes"], stats["first_start"], stats["last_end"])
        )
    return (
        "**How this plan was produced (deterministic engine)**\n\n"
        "1. **Priority**: urgent jobs first, then high, normal, low; ties broken by "
        "when the ticket was filed.\n"
        "2. **Fixed durations**: every job occupies the locked duration of its "
        "category — never estimated, never compressed.\n"
        "3. **Travel**: 15 minutes between colleges, 5 minutes within one college; "
        "the first job costs a 15-minute leg from the workshop.\n"
        "4. **Availability**: every job falls inside a window the student declared "
        "free, and never straddles the 12:00-13:30 lunch break.\n\n"
        "Today: %d jobs covering %s; %d minutes of work, %d minutes of travel; "
        "first job %s, last job ends %s."
        % (stats["jobs"], ", ".join(colleges), stats["work_minutes"],
           stats["travel_minutes"], stats["first_start"], stats["last_end"])
    )


# --------------------------------------------------------------------------- #
# Entry point
# --------------------------------------------------------------------------- #

def configure(api_key=None, model=None, base_url=None, provider=None):
    Handler.api_key = (api_key or "").strip()
    Handler.key_source = "env" if Handler.api_key else ""
    Handler.provider = provider or ""
    Handler.model = (model or os.environ.get("OPENAI_MODEL")
                     or os.environ.get("OPENROUTER_MODEL") or DEFAULT_MODEL).strip()
    set_api_base(base_url if base_url is not None else os.environ.get("OPENAI_BASE_URL"))
    ensure_seed()
    return Handler


def resolve_api_key(argv):
    for i, a in enumerate(argv):
        if a == "--api-key" and i + 1 < len(argv):
            return argv[i + 1].strip()
        if a.startswith("--api-key="):
            return a.split("=", 1)[1].strip()
    return (os.environ.get("OPENAI_API_KEY")
            or os.environ.get("OPENROUTER_API_KEY") or "").strip()


def resolve_upstream(argv):
    """Key plus the OpenAI-compatible root implied by the environment."""
    key = resolve_api_key(argv)
    base = (os.environ.get("OPENAI_BASE_URL") or "").strip()
    provider = ""
    if base:
        provider = "custom"
    elif os.environ.get("OPENAI_API_KEY") and not os.environ.get("OPENROUTER_API_KEY"):
        base = "https://api.openai.com/v1"
        provider = "openai"
    elif key:
        provider = "openrouter"
    return key, base, provider


def main():
    key, base, provider = resolve_upstream(sys.argv[1:])
    configure(key, base_url=base, provider=provider)
    try:
        port = int(os.environ.get("PORT") or 3000)
    except ValueError:
        port = 3000
    sys.stderr.write(
        "DormCare -> http://0.0.0.0:%d  (model=%s, key=%s, tickets=%d, workers=%d, data=%s, seed=%s)\n"
        % (port, Handler.model, "set" if Handler.api_key else "MISSING",
           len(STORE.tickets()), len(STORE.workers()),
           os.path.basename(DATA_FILE),
           "on" if _seed_demo_tickets_enabled() else "off")
    )
    ThreadingHTTPServer(("0.0.0.0", port), Handler).serve_forever()


if __name__ == "__main__":
    main()
