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
    if any(word in text.lower() for word in (
            "网络", "断网", "网线", "网口", "路由器", "wifi", "internet",
            "ethernet", "router")):
        classified["repair_type"] = "network"
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
        electrical = any(word in text.lower() for word in (
            "漏电", "触电", "火花", "冒烟", "起火", "electric shock",
            "sparking", "smoke", "fire"))
        gas_or_water = any(word in text.lower() for word in (
            "燃气", "煤气", "大面积漏水", "gas leak", "major flood", "flooding"))
        if lang == "zh":
            warning = "请立即远离危险区域，不要触碰或拆修设备，并联系校园保卫/应急人员。"
            if electrical:
                warning += "仅在无需靠近故障点且能安全操作时关闭总电源。"
            if gas_or_water:
                warning += "仅在阀门远离危险点且能安全操作时关闭总阀。"
        else:
            warning = ("Leave the danger area, do not touch or dismantle equipment, "
                       "and contact campus emergency/security now.")
            if electrical:
                warning += " Turn off the main power only if it is safely reachable away from the fault."
            if gas_or_water:
                warning += " Close the main valve only if it is safely reachable away from the hazard."
    return draft, enough, warning


def validate_draft(draft):
    if not isinstance(draft, dict):
        return False
    return (draft.get("repair_type") in REPAIR_BY_ID
            and draft.get("priority") in ("urgent", "high", "normal", "low")
            and bool(str(draft.get("description") or "").strip()))
