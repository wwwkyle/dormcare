#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
DormCare · 测试用 HTTP 客户端 / Cookie-aware test client
========================================================

Every endpoint now requires a session, so every test script has to sign in
before it can call anything. Rather than repeat cookie juggling in six files,
they all share this one.

    stu = Client(BASE, "2024001", "dorm2026")     # a student
    wkr = Client(BASE, "A01", "fix2026")          # a technician
    anon = Client(BASE)                            # signed out on purpose

`Client` also remembers the last status and `Location` header, which is what
lets the tests assert on redirects without following them.
"""

import json
import os
import subprocess
import sys
import urllib.error
import urllib.request

SESSION_COOKIE = "dc_session"


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Turn redirects into ordinary responses so a test can inspect them."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def spawn_server(here, port, key=None, env_extra=None, log_name=None, data=None):
    """Start `app.py` with its output going to a FILE, not a pipe.

    A pipe is the obvious choice and the wrong one. The server writes one log
    line per request; once the pipe buffer fills (4 KB on Windows) the server
    blocks on `write`, and every subsequent client request times out with no
    explanation. A file has no such limit, and the tests read it at the end to
    report a traceback if the server died.

    `key=None` leaves OPENROUTER_API_KEY alone, `key=""` removes it (which is
    how the offline path is exercised) and a non-empty key sets it.

    `data` points the server at its own store file instead of the shared
    `data/store.json`. Every suite should pass one: with the address bound to
    the account, one suite's leftover tickets are visible to the next, and
    cross-talk between suites reads as a bug in the app.

    Returns (process, path-to-log).
    """
    here = str(here)
    log_path = os.path.join(here, log_name or ("server.%d.log" % port))
    env = dict(os.environ)
    env["PORT"] = str(port)
    if key is None:
        pass
    elif key:
        env["OPENROUTER_API_KEY"] = key
    else:
        env.pop("OPENROUTER_API_KEY", None)
    if data:
        env["DORMCARE_DATA"] = str(data)
    if env_extra:
        env.update(env_extra)
    log = open(log_path, "w", encoding="utf-8")
    proc = subprocess.Popen([sys.executable, "app.py"], cwd=here, env=env,
                            stdout=log, stderr=subprocess.STDOUT, text=True)
    proc._log_handle = log          # keep the handle alive with the process
    return proc, log_path


def store_path(here, name):
    """A private store file for one suite, e.g. `data/test-smoke.json`."""
    return os.path.join(str(here), "data", "test-%s.json" % name)


#: A well-formed but empty store. Writing this is how a suite starts clean.
EMPTY_STORE = ('{"tickets": [], "workers": [], "plans": [], "users": [], '
               '"sessions": []}')


def reset_store(path):
    """Start a suite from an empty database.

    Truncating rather than deleting, on purpose: a delete is the kind of
    operation a sandbox guard may stop to ask about, and running seven suites
    in one go is enough deletions to trip one. An empty file is just as good a
    starting point, and `ensure_seed()` re-creates the accounts on boot.
    """
    try:
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(EMPTY_STORE)
    except OSError:
        try:
            os.remove(path)
        except OSError:
            pass
    return path


def server_output(log_path, tail=3000):
    try:
        with open(log_path, "r", encoding="utf-8", errors="replace") as fh:
            return fh.read()[-tail:]
    except Exception:
        return ""


class Client:
    def __init__(self, base, username=None, password=None):
        self.base = base.rstrip("/")
        self.cookie = ""
        self.user = None
        self.last_status = None
        self.last_location = ""
        self.last_set_cookie = ""
        if username:
            self.login(username, password)

    # ---- transport -------------------------------------------------------- #

    def _opener(self, follow):
        if follow:
            return urllib.request.build_opener()
        return urllib.request.build_opener(_NoRedirect)

    def _remember(self, headers):
        if not headers:
            return
        self.last_location = headers.get("Location") or self.last_location
        raw = headers.get("Set-Cookie")
        if not raw:
            return
        self.last_set_cookie = raw
        # Keep only the session pair; the attributes (Path, Max-Age, ...) are
        # not echoed back as a cookie.
        for part in raw.split(";"):
            part = part.strip()
            if part.startswith(SESSION_COOKIE + "="):
                self.cookie = part

    @staticmethod
    def _decode(raw):
        try:
            text = raw.decode("utf-8")
        except Exception:
            return {}
        if not text.strip():
            return {}
        try:
            return json.loads(text)
        except Exception:
            return {"raw": text}

    def call(self, path, body=None, method=None, follow=True, headers=None):
        data = json.dumps(body).encode("utf-8") if body is not None else None
        hdrs = {"Content-Type": "application/json"}
        if self.cookie:
            hdrs["Cookie"] = self.cookie
        if headers:
            hdrs.update(headers)
        req = urllib.request.Request(
            self.base + path, data=data,
            method=method or ("POST" if data is not None else "GET"),
            headers=hdrs)
        self.last_location = ""
        self.last_set_cookie = ""
        try:
            with self._opener(follow).open(req, timeout=180) as r:
                self.last_status = r.status
                self._remember(r.headers)
                return r.status, self._decode(r.read())
        except urllib.error.HTTPError as e:
            self.last_status = e.code
            self._remember(e.headers)
            return e.code, self._decode(e.read())

    def get_text(self, path, follow=True):
        """Raw body of a page/asset — used to assert what the server serves."""
        status, _ = self.call(path, method="GET", follow=follow)
        return status

    # ---- identity --------------------------------------------------------- #

    def login(self, username, password):
        status, data = self.call("/api/auth/login",
                                 {"username": username, "password": password})
        if status != 200:
            raise RuntimeError("sign-in failed for %r: %s %s" % (username, status, data))
        self.user = data.get("user")
        return self.user

    def register(self, username, name, password, college="", college_block="",
                 dorm="", block=None):
        """Sign a student up. The room is required — it is their address.

        `block` is accepted as an alias for `college_block` because "block"
        already means something in the request body.
        """
        status, data = self.call("/api/auth/register", {
            "username": username, "name": name, "password": password,
            "college": college, "block": college_block if block is None else block,
            "dorm": dorm})
        if status == 200:
            self.user = data.get("user")
        return status, data

    def logout(self):
        status, data = self.call("/api/auth/logout", {})
        self.user = None
        return status, data


#: The seeded accounts, in one place so the tests agree with app.SEED_USERS.
DEMO_STUDENT = ("2024001", "dorm2026")
DEMO_STUDENT_2 = ("2024002", "dorm2026")
DEMO_STUDENT_PASSWORD = "dorm2026"
DEMO_WORKERS = [("A01", "fix2026"), ("B02", "fix2026"), ("C03", "fix2026")]
DEMO_ADMIN = ("admin", "admin2026")
DEMO_ADMIN_PASSWORD = "admin2026"

#: Every page the server serves, and the ONE role allowed to open it.
#:
#: The three consoles were once a page each. Every menu card now opens a page
#: that does exactly one thing, so there are eleven pages instead of four — and
#: each is still gated to a single role. The suites read this map rather than
#: hard-coding paths, so adding a page cannot quietly leave the gate untested.
PAGE_OWNER = {
    # the one page everybody signed in may open
    "/menu.html": None,
    "/menu.js": None,
    # the password half is valid for every role, so this one is too
    "/account.html": None,
    "/account.js": None,
    # student
    "/report.html": "student",
    "/student-report.js": "student",
    "/records.html": "student",
    "/student-records.js": "student",
    # technician
    "/plan.html": "worker",
    "/worker-plan.js": "worker",
    "/jobs.html": "worker",
    "/worker-jobs.js": "worker",
    # administrator
    "/overview.html": "admin",
    "/admin-overview.js": "admin",
    "/tickets.html": "admin",
    "/admin-tickets.js": "admin",
    "/users.html": "admin",
    "/admin-users.js": "admin",
    "/roster.html": "admin",
    "/admin-roster.js": "admin",
}

#: The consolidated consoles the split removed. They must be GONE, not quietly
#: still served — a leftover `/index.html` would be an un-gated second copy of
#: the student console the moment somebody re-registers it.
RETIRED_PAGES = ("/index.html", "/worker.html", "/admin.html",
                 "/student.js", "/worker.js", "/admin.js")

ROLES = ("student", "worker", "admin")

#: account -> (college, block, dorm).
#:
#: One account per room, and the room is the address of every ticket that
#: account files. Tests that want a workload spread over several colleges
#: therefore need several residents — which is the whole point of the design.
DEMO_STUDENTS = {
    "2024001": ("shaw", "东座 1 栋", "A-301"),
    "2024009": ("shaw", "西座 2 栋", "A-415"),
    "2024003": ("muse", "", "B-102"),
    "2024010": ("muse", "", "B-205"),
    "2024004": ("harmonia", "宿舍 2 栋", "C-410"),
    "2024005": ("ling", "C 座", "D-201"),
    "2024006": ("minerva", "南区", "E-508"),
    "2024002": ("diligentia", "A 座", "F-303"),
    "2024007": ("eighth", "北区", "G-101"),
    "2024008": ("duan", "", "H-702"),
}


def account_at(college, dorm):
    """Which seeded account lives in `college`/`dorm`."""
    for account, (c, _b, d) in DEMO_STUDENTS.items():
        if c == college and d == dorm:
            return account
    raise KeyError("no seeded student lives in %s %s" % (college, dorm))


class Demo:
    """One signed-in client per seeded room, created on first use.

    A ticket's address is the room on the account that filed it, so a workload
    spanning several colleges has to be filed by the resident of each address.
    This is what lets the tests keep writing a flat list of jobs.

        demo = Demo(BASE)
        demo.clear()
        demo.at("ling", "D-201").call("/api/tickets", {...})
    """

    def __init__(self, base):
        self.base = base
        self._clients = {}

    def at(self, college="", dorm="", account=""):
        if not account:
            account = account_at(college, dorm)
        if account not in self._clients:
            self._clients[account] = Client(self.base, account,
                                            DEMO_STUDENT_PASSWORD)
        return self._clients[account]

    def clear(self):
        """Delete every ticket — each account dropping its own.

        Deliberately NOT done through the administrator: the point of this
        helper is to exercise the real, ownership-checked delete path that a
        student would use, and a suite that wiped the table from the operator's
        desk would stop noticing if that path broke. `Admin.clear_tickets()` is
        there for suites that want the shortcut instead.
        """
        dropped = 0
        for account in DEMO_STUDENTS:
            cli = self.at(account=account)
            for t in cli.call("/api/tickets")[1].get("tickets", []):
                cli.call("/api/tickets/delete", {"id": t["id"]})
                dropped += 1
        return dropped


class Admin:
    """The operator's desk, for a suite that wants to set the board up fast.

        board = Admin(BASE)
        board.clear_tickets()

    An administrator may remove ANY ticket, which is exactly what makes this a
    convenient reset — and exactly why it must not be how the other suites
    exercise the student's own delete path.
    """

    def __init__(self, base):
        self.client = Client(base, *DEMO_ADMIN)

    def clear_tickets(self):
        dropped = 0
        for t in self.client.call("/api/admin/tickets")[1].get("tickets", []):
            self.client.call("/api/admin/ticket/delete", {"id": t["id"]})
            dropped += 1
        return dropped
