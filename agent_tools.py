"""Deterministic business tools used by the intake agent."""
from datetime import date

from ai_agents import fallback_classify
from config import REPAIR_BY_ID

HIGH_RISK = ("漏电", "触电", "火花", "燃气", "煤气", "冒烟", "起火", "大面积漏水",
             "electrical leak", "electric shock", "gas leak", "smoke", "fire",
             "sparking", "major flood", "flooding")


def build_draft(history, image_ids, user, lang="zh"):
    text = "\n".join(str(x.get("content") or "") for x in history
                     if x.get("role") == "user").strip()
    classified = fallback_classify(text, lang)
    risky = any(word in text.lower() for word in HIGH_RISK)
    if risky:
        classified["priority"] = "urgent"
    if any(word in text.lower() for word in (
            "漏电", "触电", "火花", "插座", "sparking", "socket", "electric shock")):
        classified["repair_type"] = "power"
    location = " ".join(x for x in (
        user.get("block", ""), user.get("dorm", "")) if x)
    enough = len(text) >= (12 if lang == "zh" else 30)
    draft = {
        "title": text.splitlines()[0][:60] if text else "",
        "repair_type": classified["repair_type"],
        "description": text[:1000],
        "location": location,
        "priority": classified["priority"],
        "image_ids": list(dict.fromkeys(image_ids or []))[:5],
        "expected_date": date.today().isoformat(),
    }
    warning = ""
    if risky:
        warning = ("请立即远离危险区域，不要触碰设备，并联系校园保卫/应急电话。"
                   if lang == "zh" else
                   "Leave the danger area, do not touch equipment, and contact campus emergency/security now.")
    return draft, enough, warning


def validate_draft(draft):
    if not isinstance(draft, dict):
        return False
    return (draft.get("repair_type") in REPAIR_BY_ID
            and draft.get("priority") in ("urgent", "high", "normal", "low")
            and bool(str(draft.get("description") or "").strip()))
