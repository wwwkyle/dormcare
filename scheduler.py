#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
DormCare · 排班引擎 / Deterministic scheduling engine
=====================================================

Why this file exists
--------------------
The requirement is: "不同的类别的预计维修时间不一样，这个要固定" and "书院之间
往返设定为 15 分钟，书院内设定为 5 分钟". Fixed numbers must produce a fixed
plan. A language model cannot be trusted with that, so it is not asked.

The model does exactly two jobs elsewhere in this app:
    1. read a student's free-text complaint and classify it (RAG), and
    2. explain a finished plan in natural language.
Every minute of the schedule is computed HERE, by plain arithmetic.

Constraints modelled
--------------------
* One worker per shift (the day is a single non-preemptive sequence).
* Which worker gets which ticket is decided here too (`assign_day`), by trade
  match + certification + least-loaded — never by the model.
* A repair occupies its FIXED duration: travel -> work -> travel -> work ...
* Travel: 5 min when the next repair is in the same college as the previous one,
  15 min otherwise. The first repair of the day is 15 min from the workshop.
* Shift window 08:30-18:00, lunch break 12:00-13:30 (no work may span it).
* A repair can only be scheduled inside a window the STUDENT DECLARED FREE.
* Priority orders the queue; it never overrides the two hard constraints above.
"""

from datetime import datetime, date, time, timedelta

from config import (COLLEGES, COLLEGE_BY_ID, REPAIR_BY_ID, TRAVEL_RULES,
                    repair_minutes, repair_label, college_label)

# --------------------------------------------------------------------------- #
# 优先级 / Priority
#
# Single source of truth for both ordering and display. Higher number = more
# urgent. The engine sorts by (priority desc, requested_date asc, created_at asc)
# so the rule is: urgent first, then longest-waiting, then FIFO.
# --------------------------------------------------------------------------- #

PRIORITY_LEVELS = {
    "urgent": {"rank": 3, "zh": "紧急", "en": "Urgent",
               "zh_desc": "漏水、断电、严重影响居住安全",
               "en_desc": "Leak, power loss, safety-affecting"},
    "high":   {"rank": 2, "zh": "较急", "en": "High",
               "zh_desc": "空调不制冷、下水道堵塞等影响正常生活",
               "en_desc": "AC failure, blocked drain, disrupts daily life"},
    "normal": {"rank": 1, "zh": "一般", "en": "Normal",
               "zh_desc": "家具松动、灯具故障等可由自主处理",
               "en_desc": "Loose furniture, failed lighting"},
    "low":    {"rank": 0, "zh": "不急", "en": "Low",
               "zh_desc": "外观或使用体验问题，可等待",
               "en_desc": "Cosmetic or convenience issue"},
}

SLOT_MINUTES = 30          # 空闲时间以 30 分钟为一格 / availability grid step


# --------------------------------------------------------------------------- #
# 时间工具 / Time helpers
# --------------------------------------------------------------------------- #

def _hm(text):
    """'08:30' -> time(8, 30)"""
    h, m = text.split(":")
    return time(int(h), int(m))


def _combine(day, text):
    return datetime.combine(day, _hm(text))


def _fmt(dt, lang="zh"):
    if lang == "zh":
        return dt.strftime("%H:%M")
    return dt.strftime("%H:%M")


def _fmt_long(dt, lang="zh"):
    weekdays_zh = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
    weekdays_en = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    if lang == "zh":
        return "%d月%d日 %s" % (dt.month, dt.day, weekdays_zh[dt.weekday()])
    return "%s %s %d" % (weekdays_en[dt.weekday()], dt.strftime("%b"), dt.day)


def next_workdays(start_day, count):
    """The next `count` weekdays starting at `start_day` (inclusive)."""
    out, cur = [], start_day
    while len(out) < count:
        if cur.weekday() < 5:
            out.append(cur)
        cur += timedelta(days=1)
    return out


def slot_label(day_iso, slot_index, lang="zh"):
    """slot_index 0 == 08:00-08:30, 16 == 16:00-16:30."""
    start = _combine(date.fromisoformat(day_iso), "08:00") + timedelta(minutes=slot_index * SLOT_MINUTES)
    end = start + timedelta(minutes=SLOT_MINUTES)
    return "%s-%s" % (start.strftime("%H:%M"), end.strftime("%H:%M"))


def slot_range(day_iso, slot_index):
    """Absolute datetime range for an availability slot."""
    start = _combine(date.fromisoformat(day_iso), "08:00") + timedelta(minutes=slot_index * SLOT_MINUTES)
    return start, start + timedelta(minutes=SLOT_MINUTES)


def travel_minutes(from_college, to_college):
    """15 minutes between colleges, 5 minutes within the same college.

    `None` represents the workshop, which sits outside every college, so the
    first job of the day always costs a full cross-college leg.
    """
    if from_college is None or to_college is None:
        return TRAVEL_RULES["cross_college_minutes"]
    if from_college == to_college:
        return TRAVEL_RULES["same_college_minutes"]
    return TRAVEL_RULES["cross_college_minutes"]


# --------------------------------------------------------------------------- #
# 空闲时间合并 / Availability merging
# --------------------------------------------------------------------------- #

def merge_ranges(ranges):
    """Merge overlapping/adjacent (start, end) tuples into disjoint ranges."""
    clean = []
    for s, e in ranges:
        if e > s:
            clean.append((s, e))
    if not clean:
        return []
    clean.sort(key=lambda r: r[0])
    out = [clean[0]]
    for s, e in clean[1:]:
        if s <= out[-1][1]:
            out[-1] = (out[-1][0], max(out[-1][1], e))
        else:
            out.append((s, e))
    return out


def ticket_windows(ticket):
    """Availability windows of a ticket as absolute datetime ranges on its date."""
    day = date.fromisoformat(ticket["date"])
    raw = []
    for s_idx, e_idx in ticket.get("slots") or []:
        s, _ = slot_range(ticket["date"], s_idx)
        _, e = slot_range(ticket["date"], e_idx)
        raw.append((s, e))
    return merge_ranges(raw)


def fits_window(start, end, windows):
    """True when [start, end) lies entirely inside ONE declared window."""
    if not windows:
        return False
    for ws, we in windows:
        if start >= ws and end <= we:
            return True
    return False


def intersects_lunch(start, end):
    """A job may not straddle the lunch break."""
    lunch_s = _combine(start.date(), TRAVEL_RULES["lunch_start"])
    lunch_e = _combine(start.date(), TRAVEL_RULES["lunch_end"])
    return start < lunch_e and end > lunch_s


# --------------------------------------------------------------------------- #
# 核心：贪心排班 / The core greedy scheduler
#
# The queue entry point is `ticket["ready"]` — the earliest instant the worker
# may START the job, which already includes the travel leg from the previous
# job. Two adjustments keep that instant honest at each visit:
#
#   * the worker cannot clock in before 08:30             -> clamp to shift start
#   * the worker must pause for lunch if it would overlap  -> push past 13:30
#
# After the job we always add the FIXED duration plus the travel leg to the
# NEXT job, and we never let the next job start before its own window opens.
# --------------------------------------------------------------------------- #

def _align_to_ready(ready, job_date):
    """Clamp a proposed start to shift start / lunch / window opening.

    Returns (new_start, new_ready) or (None, ready) when the job cannot be
    placed on `job_date` at all (it would overflow the shift).
    """
    shift_start = _combine(job_date, TRAVEL_RULES["workday_start"])
    shift_end = _combine(job_date, TRAVEL_RULES["workday_end"])

    if ready < shift_start:
        ready = shift_start

    # Lunch: the pause starts at 12:00, so a job that would run into it is
    # pushed to 13:30 — but only if the job itself starts before lunch.
    if ready < _combine(job_date, TRAVEL_RULES["lunch_end"]):
        probe_end = ready + timedelta(minutes=1)
        if intersects_lunch(ready, probe_end):
            ready = _combine(job_date, TRAVEL_RULES["lunch_end"])

    if ready >= shift_end:
        return None, ready
    return ready, ready


def _advance(job, include_travel_to, next_college):
    """ready after finishing `job` at `job["end"]`, travelling to `next_college`."""
    if include_travel_to is None:
        return job["end"]
    return job["end"] + timedelta(minutes=travel_minutes(include_travel_to, next_college))


def _try_place(t, day, ready, shift_end, windows, duration):
    """Find the first admissible start for ticket `t` at or after `ready`.

    Returns a datetime, or None when the job cannot go anywhere today.
    Admissibility means all three hold at once:
        * inside the student's declared window,
        * inside the shift,
        * not straddling the lunch break.
    """
    for _ in range(len(windows) + 3):
        cand, _ = _align_to_ready(ready, day)
        if cand is None:
            return None
        if cand + timedelta(minutes=duration) > shift_end:
            return None
        if intersects_lunch(cand, cand + timedelta(minutes=duration)):
            ready = _combine(day, TRAVEL_RULES["lunch_end"])
            continue
        if fits_window(cand, cand + timedelta(minutes=duration), windows):
            return cand
        # Not inside a declared window: jump to the first window opening after
        # the current candidate and retry from there.
        nxt = [ws for ws, _ in windows if ws >= cand]
        if not nxt:
            return None
        ready, _ = _align_to_ready(min(nxt), day)
        if ready is None:
            return None
    return None


def _travel_insert_cost(prev_college, college, next_college):
    """Extra travel minutes incurred by inserting `college` between two stops.

    Inserting a stop between two jobs replaces one direct leg with two legs, so
    the cost is (in + out) - (original direct leg). Used by the second pass to
    prefer fill-ins that do not scatter the route.
    """
    direct = (travel_minutes(prev_college, next_college)
              if (prev_college is not None and next_college is not None) else 0)
    return travel_minutes(prev_college, college) + travel_minutes(college, next_college) - direct


def duration_exceeds_all_windows(duration, windows):
    """True when the fixed repair time cannot fit in ANY declared window.

    Such a ticket is genuinely impossible to honour as filed — the student
    needs to widen their free time or split it, which is a different message
    from "the shift is full".
    """
    for ws, we in windows:
        if (we - ws).total_seconds() / 60 >= duration:
            return False
    return True


def _make_job(t, rtype, day_iso, start, duration, prev_college, lang):
    """Build the job record handed to the UI and to the AI agents."""
    return {
        "ticket_id": t["id"],
        "date": day_iso,
        "start": start,
        "end": start + timedelta(minutes=duration),
        "duration": duration,
        "travel_in": travel_minutes(prev_college, t["college"]),
        "college": t["college"],
        "college_label": college_label(t["college"], lang),
        "block": t.get("block", ""),
        "dorm": t["dorm"],
        "location": " ".join(x for x in (t.get("block"), t.get("dorm")) if x),
        "repair_type": rtype["id"],
        "repair_label": repair_label(rtype["id"], lang),
        "priority": t.get("priority", "normal"),
        "student": t.get("student_name", ""),
        "student_contact": t.get("contact", ""),
        "symptom": t.get("description", ""),
        "parts": rtype["parts"] if lang == "zh" else rtype["parts_en"],
        "certified": bool(rtype.get("certified")),
        "notes": t.get("notes", ""),
        "status": "scheduled",
    }


def schedule_day(day_iso, tickets, worker=None, lang="zh"):
    """Plan one worker's day. Returns (jobs, unscheduled, stats).

    Two passes, and the reason for the second one matters:

    PASS 1 walks the queue in priority order and appends each job to the end of
    the day. That is the right ordering rule, but appending blindly can strand a
    job whose declared window sits earlier than the current cursor — a job
    skipped here would stay skipped even though the shift has hours of room left.

    PASS 2 therefore reconsiders every skipped job and inserts it into the FIRST
    gap where it actually fits, choosing the cheapest insertion (least extra
    travel). Priority order is preserved because pass 2 only ever fills space
    that pass 1 left unused — it can never displace a scheduled job.
    """
    day = date.fromisoformat(day_iso)
    shift_start = _combine(day, TRAVEL_RULES["workday_start"])
    shift_end = _combine(day, TRAVEL_RULES["workday_end"])

    order = sorted(
        [t for t in tickets if t["date"] == day_iso],
        key=lambda t: (-PRIORITY_LEVELS.get(t.get("priority", "normal"), PRIORITY_LEVELS["normal"])["rank"],
                       t.get("created_at") or "", t.get("id") or ""),
    )

    jobs, skipped = [], []
    cursor = shift_start          # instant the worker becomes available
    prev_college = None           # None == at the workshop

    # ---------------------------- PASS 1: append in priority order --------- #
    for t in order:
        rtype = REPAIR_BY_ID.get(t.get("repair_type")) or REPAIR_BY_ID["other"]
        duration = rtype["minutes"]
        windows = ticket_windows(t)
        if not windows:
            skipped.append((t, "no_availability"))
            continue

        ready = cursor + timedelta(minutes=travel_minutes(prev_college, t["college"]))
        start = _try_place(t, day, ready, shift_end, windows, duration)
        if start is None:
            skipped.append((t, None))       # reason decided after pass 2
            continue

        jobs.append(_make_job(t, rtype, day_iso, start, duration, prev_college, lang))
        cursor = start + timedelta(minutes=duration)
        prev_college = t["college"]

    # ------------------- PASS 2: insert skipped jobs into real gaps -------- #
    # Recompute every stop's absolute start/end so gap arithmetic is exact.
    for j in jobs:
        j["_start"] = j["start"]
        j["_end"] = j["end"]

    for t, _ in skipped:
        rtype = REPAIR_BY_ID.get(t.get("repair_type")) or REPAIR_BY_ID["other"]
        duration = rtype["minutes"]
        windows = ticket_windows(t)
        if not windows:
            continue

        best = None
        n = len(jobs)
        for pos in range(n + 1):
            # the leg arriving at `pos` (from the previous stop or the workshop)
            prev_c = jobs[pos - 1]["college"] if pos > 0 else None
            next_c = jobs[pos]["college"] if pos < n else None
            # earliest this job can begin, given where it sits in the route
            if pos > 0:
                arrive = jobs[pos - 1]["_end"] + timedelta(
                    minutes=travel_minutes(prev_c, t["college"]))
                if pos < n:
                    # must finish in time to still reach the following stop
                    latest_end = jobs[pos]["_start"] - timedelta(
                        minutes=travel_minutes(t["college"], next_c))
                else:
                    latest_end = shift_end
            else:
                arrive = shift_start + timedelta(
                    minutes=travel_minutes(None, t["college"]))
                latest_end = jobs[0]["_start"] - timedelta(
                    minutes=travel_minutes(t["college"], next_c)) if n else shift_end

            start = _try_place(t, day, arrive, latest_end, windows, duration)
            if start is None:
                continue
            cost = _travel_insert_cost(prev_c, t["college"], next_c)
            if best is None or cost < best[0]:
                best = (cost, pos, start)

        if best is None:
            continue
        _, pos, start = best
        job = _make_job(t, rtype, day_iso, start, duration,
                        jobs[pos - 1]["college"] if pos > 0 else None, lang)
        job["_start"], job["_end"] = start, start + timedelta(minutes=duration)
        jobs.insert(pos, job)

    # Re-number and recompute travel legs from the final ordering.
    for i, j in enumerate(jobs):
        j["seq"] = i + 1
        j.pop("_start", None)
        j.pop("_end", None)
        j["travel_in"] = travel_minutes(
            jobs[i - 1]["college"] if i > 0 else None, j["college"])
        j["travel_out"] = travel_minutes(
            j["college"], jobs[i + 1]["college"] if i + 1 < len(jobs) else None)

    # Only now can the reason for each unplaced ticket be stated honestly.
    placed_ids = {j["ticket_id"] for j in jobs}
    unscheduled = []
    for t, _ in skipped:
        if t["id"] in placed_ids:
            continue
        windows = ticket_windows(t)
        rtype = REPAIR_BY_ID.get(t.get("repair_type")) or REPAIR_BY_ID["other"]
        if not windows:
            reason = "no_availability"
        elif duration_exceeds_all_windows(rtype["minutes"], windows):
            reason = "window_too_short"
        else:
            reason = "no_time_left"
        unscheduled.append((t, reason))

    work_total = sum(j["duration"] for j in jobs)
    travel_total = sum(j["travel_in"] for j in jobs)
    busy_end = jobs[-1]["end"] if jobs else (jobs and shift_start)
    stats = {
        "jobs": len(jobs),
        "unscheduled": len(unscheduled),
        "work_minutes": work_total,
        "travel_minutes": travel_total,
        "span_minutes": (int((jobs[-1]["end"] - shift_start).total_seconds() // 60)
                         if jobs else 0),
        "first_start": jobs[0]["start"].strftime("%H:%M") if jobs else None,
        "last_end": jobs[-1]["end"].strftime("%H:%M") if jobs else None,
        "idle_minutes": max(0, int((shift_end - shift_start).total_seconds() // 60)
                            - work_total - travel_total - 90),   # minus lunch
        "shift_start": TRAVEL_RULES["workday_start"],
        "shift_end": TRAVEL_RULES["workday_end"],
    }
    return jobs, unscheduled, stats


# --------------------------------------------------------------------------- #
# 派单 / Ticket -> technician assignment
#
# Why this exists
# ---------------
# Once technicians log in, each of them may only see their OWN jobs. That means
# something has to decide whose job each ticket is — and, as with everything
# else in this file, the decision must be arithmetic rather than a model call,
# so two people looking at the same day always see the same split.
#
# Rules, in order:
#   1. the technician's trade must cover the repair type (`specialties`);
#   2. a repair flagged `certified` (HVAC, mains power, appliances) may only go
#      to a certified technician — if none matches on trade, any certified one;
#   3. among the eligible, the least-loaded technician wins, ties broken by the
#      order the technicians were seeded in.
#
# Job-by-job it is a greedy load balancer; end to end it is deterministic.
# --------------------------------------------------------------------------- #

def assign_day(day_iso, tickets, workers):
    """Hand each of `day_iso`'s open tickets to exactly one technician.

    Returns a list of (worker_id, [tickets]) preserving the `workers` order,
    so the caller can render "my day" without seeing anybody else's queue.
    """
    buckets = [(w["id"], []) for w in workers]
    if not buckets:
        return []
    load = {wid: 0 for wid, _ in buckets}
    index = {wid: i for i, (wid, _) in enumerate(buckets)}
    by_id = {w["id"]: w for w in workers}

    open_tickets = [
        t for t in tickets
        if t.get("date") == day_iso and t.get("status") not in ("done", "confirmed")
    ]
    order = sorted(
        open_tickets,
        key=lambda t: (-PRIORITY_LEVELS.get(t.get("priority", "normal"),
                                            PRIORITY_LEVELS["normal"])["rank"],
                       t.get("created_at") or "", t.get("id") or ""),
    )

    for t in order:
        repair = t.get("repair_type")
        rtype = REPAIR_BY_ID.get(repair) or REPAIR_BY_ID["other"]
        eligible = [w for w in workers if repair in (w.get("specialties") or [])]
        if rtype.get("certified"):
            certified = [w for w in eligible if w.get("certified")]
            eligible = certified or [w for w in workers if w.get("certified")]
        if not eligible:
            # Nobody claims this trade — fall back to the generalist, then to
            # the whole roster, rather than silently dropping the ticket.
            eligible = [w for w in workers if "other" in (w.get("specialties") or [])]
        if not eligible:
            eligible = list(workers)

        chosen = min(eligible, key=lambda w: (load[w["id"]], index[w["id"]]))
        for wid, bucket in buckets:
            if wid == chosen["id"]:
                bucket.append(t)
                load[wid] += 1
                break

    return buckets


def schedule_index(day_iso, tickets, workers, lang="zh"):
    """`{ticket_id: {"worker_id", "start", "end"}}` for one whole day.

    One pass over the roster: assign, then plan each technician's day with the
    ordinary engine. That is what lets the student side answer "谁来做、几点来"
    without the student ever being able to read the rest of the queue.
    """
    out = {}
    for wid, mine in assign_day(day_iso, tickets, workers):
        if not mine:
            continue
        worker = next((w for w in workers if w["id"] == wid), None)
        jobs, _unscheduled, _stats = schedule_day(day_iso, mine, worker, lang)
        for j in jobs:
            out[j["ticket_id"]] = {
                "worker_id": wid,
                "start": j["start"].strftime("%H:%M"),
                "end": j["end"].strftime("%H:%M"),
                "seq": j["seq"],
            }
    return out


# --------------------------------------------------------------------------- #
# 序列化 / Serialisation for the browser
# --------------------------------------------------------------------------- #

def job_to_json(job, lang="zh"):
    return {
        "seq": job["seq"],
        "ticket_id": job["ticket_id"],
        "date": job["date"],
        "start": job["start"].strftime("%H:%M"),
        "end": job["end"].strftime("%H:%M"),
        "start_iso": job["start"].isoformat(),
        "end_iso": job["end"].isoformat(),
        "duration": job["duration"],
        "travel_in": job["travel_in"],
        "travel_out": job.get("travel_out", 0),
        "college": job["college"],
        "college_label": job["college_label"],
        "block": job.get("block", ""),
        "dorm": job["dorm"],
        "location": job.get("location", job["dorm"]),
        "repair_type": job["repair_type"],
        "repair_label": job["repair_label"],
        "priority": job["priority"],
        "student": job["student"],
        "student_contact": job["student_contact"],
        "symptom": job["symptom"],
        "parts": job["parts"],
        "certified": job["certified"],
        "notes": job["notes"],
    }
