#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
DormCare · 数据存储 / Storage layer
===================================

A tiny JSON-file store. No database, no third-party package — the whole app
runs on the Python standard library so it deploys anywhere without an install
step.

Collections: `tickets`, `workers`, `plans`, `users`, `sessions`, `uploads`,
`agent_sessions`. Accounts and
sessions share the file so a restart keeps everyone signed in; passwords are
stored as PBKDF2 digests (see auth.py), never in clear text.

Concurrency: a single process-wide lock guards every read-modify-write, which
is sufficient for a ThreadingHTTPServer and keeps the file always-valid JSON.
"""

import json
import os
import threading
import uuid
from datetime import datetime

_lock = threading.RLock()


class Store:
    def __init__(self, path):
        self.path = path
        parent = os.path.dirname(os.path.abspath(path))
        if parent and not os.path.isdir(parent):
            os.makedirs(parent, exist_ok=True)
        self._data = None
        self.load()

    # ---- io -------------------------------------------------------------- #

    def load(self):
        with _lock:
            if os.path.isfile(self.path):
                try:
                    with open(self.path, "r", encoding="utf-8") as fh:
                        self._data = json.load(fh)
                except Exception:
                    self._data = None
            if not isinstance(self._data, dict):
                self._data = {"tickets": [], "workers": [], "plans": [],
                              "users": [], "sessions": [], "uploads": [],
                              "agent_sessions": [], "agent_events": []}
            for key in ("tickets", "workers", "plans", "users", "sessions",
                        "uploads", "agent_sessions", "agent_events"):
                self._data.setdefault(key, [])
            return self._data

    def save(self):
        with _lock:
            tmp = self.path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(self._data, fh, ensure_ascii=False, indent=2)
            os.replace(tmp, self.path)

    # ---- tickets --------------------------------------------------------- #

    def tickets(self):
        with _lock:
            return list(self._data["tickets"])

    def ticket(self, ticket_id):
        with _lock:
            for t in self._data["tickets"]:
                if t["id"] == ticket_id:
                    return t
            return None

    def add_ticket(self, ticket):
        with _lock:
            ticket.setdefault("id", "T" + uuid.uuid4().hex[:8].upper())
            ticket.setdefault("created_at", datetime.now().isoformat(timespec="seconds"))
            ticket.setdefault("status", "pending")
            self._data["tickets"].append(ticket)
            self.save()
            return ticket

    def update_ticket(self, ticket_id, patch):
        with _lock:
            for t in self._data["tickets"]:
                if t["id"] == ticket_id:
                    t.update(patch)
                    self.save()
                    return t
            return None

    def delete_ticket(self, ticket_id):
        with _lock:
            before = len(self._data["tickets"])
            self._data["tickets"] = [t for t in self._data["tickets"] if t["id"] != ticket_id]
            self.save()
            return len(self._data["tickets"]) < before

    # ---- uploads -------------------------------------------------------- #

    def uploads(self):
        with _lock:
            return list(self._data["uploads"])

    def upload(self, upload_id):
        with _lock:
            return next((x for x in self._data["uploads"]
                         if x.get("id") == upload_id), None)

    def add_upload(self, upload):
        with _lock:
            upload.setdefault("id", "F" + uuid.uuid4().hex.upper())
            upload.setdefault("created_at", datetime.now().isoformat(timespec="seconds"))
            self._data["uploads"].append(upload)
            self.save()
            return upload

    def update_upload(self, upload_id, patch):
        with _lock:
            item = self.upload(upload_id)
            if item:
                item.update(patch)
                self.save()
            return item

    # ---- intake agent sessions ----------------------------------------- #

    def agent_sessions(self):
        with _lock:
            return list(self._data["agent_sessions"])

    def agent_session(self, session_id):
        with _lock:
            return next((x for x in self._data["agent_sessions"]
                         if x.get("id") == session_id), None)

    def add_agent_session(self, session):
        with _lock:
            session.setdefault("id", "A" + uuid.uuid4().hex.upper())
            session.setdefault("lang", "zh")
            session.setdefault("created_at", datetime.now().isoformat(timespec="seconds"))
            session.setdefault("updated_at", session["created_at"])
            session.setdefault("history", [])
            self._data["agent_sessions"].append(session)
            self.save()
            return session

    def update_agent_session(self, session_id, patch):
        with _lock:
            item = self.agent_session(session_id)
            if item:
                item.update(patch)
                item["updated_at"] = datetime.now().isoformat(timespec="seconds")
                self.save()
            return item

    def delete_agent_session(self, session_id):
        with _lock:
            before = len(self._data["agent_sessions"])
            self._data["agent_sessions"] = [
                x for x in self._data["agent_sessions"] if x.get("id") != session_id]
            changed = len(self._data["agent_sessions"]) < before
            if changed:
                self.save()
            return changed

    def add_agent_event(self, event):
        """Persist a privacy-minimal product event; never store free text."""
        with _lock:
            event.setdefault("id", "E" + uuid.uuid4().hex.upper())
            event.setdefault("created_at", datetime.now().isoformat(timespec="seconds"))
            self._data["agent_events"].append(event)
            self._data["agent_events"] = self._data["agent_events"][-10000:]
            self.save()
            return event

    # ---- workers --------------------------------------------------------- #

    def workers(self):
        with _lock:
            return list(self._data["workers"])

    def worker(self, worker_id):
        with _lock:
            for w in self._data["workers"]:
                if w["id"] == worker_id:
                    return w
            return None

    def add_worker(self, worker):
        with _lock:
            worker.setdefault("id", "W" + uuid.uuid4().hex[:6].upper())
            self._data["workers"].append(worker)
            self.save()
            return worker

    def delete_worker(self, worker_id):
        with _lock:
            before = len(self._data["workers"])
            self._data["workers"] = [w for w in self._data["workers"] if w["id"] != worker_id]
            self.save()
            return len(self._data["workers"]) < before

    # ---- plans ----------------------------------------------------------- #

    def save_plan(self, plan):
        """Keep the newest plan per (worker, date)."""
        with _lock:
            self._data["plans"] = [
                p for p in self._data["plans"]
                if not (p["worker_id"] == plan["worker_id"] and p["date"] == plan["date"])
            ]
            self._data["plans"].append(plan)
            self.save()
            return plan

    def plan(self, worker_id, day_iso):
        with _lock:
            for p in reversed(self._data["plans"]):
                if p["worker_id"] == worker_id and p["date"] == day_iso:
                    return p
            return None

    def plans(self):
        with _lock:
            return list(self._data["plans"])

    def clear_plans(self):
        with _lock:
            self._data["plans"] = []
            self.save()

    # ---- users ----------------------------------------------------------- #
    #
    # Accounts live in the same JSON document. The stored record is
    # {id, role, username, name, college?, worker_id?, password, created_at};
    # `password` is a PBKDF2 digest, never the plaintext (see auth.py).

    def users(self):
        with _lock:
            return list(self._data["users"])

    def user(self, user_id):
        with _lock:
            for u in self._data["users"]:
                if u["id"] == user_id:
                    return u
            return None

    def user_by_username(self, username):
        """Case-insensitive lookup — student numbers get typed both ways."""
        needle = (username or "").strip().lower()
        with _lock:
            for u in self._data["users"]:
                if (u.get("username") or "").lower() == needle:
                    return u
            return None

    def add_user(self, user):
        with _lock:
            user.setdefault("id", "U" + uuid.uuid4().hex[:8].upper())
            user.setdefault("created_at", datetime.now().isoformat(timespec="seconds"))
            self._data["users"].append(user)
            self.save()
            return user

    def update_user(self, user_id, patch):
        with _lock:
            for u in self._data["users"]:
                if u["id"] == user_id:
                    u.update(patch)
                    self.save()
                    return u
            return None

    # ---- sessions -------------------------------------------------------- #
    #
    # Opaque random tokens -> {user_id, role, expires_at}. Persisted so a
    # restart does not log everyone out mid-demo; purge_expired() keeps the
    # file from growing without bound.

    def sessions(self):
        with _lock:
            return list(self._data["sessions"])

    def session(self, token):
        if not token:
            return None
        with _lock:
            for s in self._data["sessions"]:
                if s.get("token") == token:
                    return s
            return None

    def add_session(self, session):
        with _lock:
            self._data["sessions"].append(session)
            self.save()
            return session

    def delete_session(self, token):
        with _lock:
            before = len(self._data["sessions"])
            self._data["sessions"] = [
                s for s in self._data["sessions"] if s.get("token") != token]
            changed = len(self._data["sessions"]) < before
            if changed:
                self.save()
            return changed

    def delete_sessions_for_user(self, user_id, keep_token=""):
        """Drop every session belonging to one account.

        Used when a password changes: a change that leaves the old session
        alive is not a change. `keep_token` spares one session — the caller's
        own — so changing your own password does not sign you out of the tab
        you are doing it in, while every other device is signed out.

        Returns how many were revoked.
        """
        with _lock:
            before = len(self._data["sessions"])
            self._data["sessions"] = [
                s for s in self._data["sessions"]
                if s.get("user_id") != user_id or s.get("token") == keep_token]
            removed = before - len(self._data["sessions"])
            if removed:
                self.save()
            return removed

    def purge_expired_sessions(self, now=None):
        """Drop every expired session. Returns how many were removed."""
        now = now or datetime.now()
        with _lock:
            keep = []
            for s in self._data["sessions"]:
                try:
                    alive = datetime.fromisoformat(s["expires_at"]) > now
                except Exception:
                    alive = False
                if alive:
                    keep.append(s)
            removed = len(self._data["sessions"]) - len(keep)
            if removed:
                self._data["sessions"] = keep
                self.save()
            return removed

