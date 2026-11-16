"""Stateless integration contract for Dify and other workflow engines."""
import hmac
from datetime import date

from agent_tools import build_draft
from config import REPAIR_BY_ID
from knowledge_base import advice_for_student


def authorized(request_key, configured_key):
    """Compare the integration key without leaking timing information."""
    return bool(configured_key) and bool(request_key) and hmac.compare_digest(
        str(request_key), str(configured_key))


def _clean_location(body):
    location = body.get("location") or {}
    if not isinstance(location, dict):
        location = {}
    return {
        "college": str(location.get("college") or body.get("college") or "")[:80],
        "block": str(location.get("block") or body.get("block") or "")[:80],
        "dorm": str(location.get("dorm") or body.get("dorm") or "")[:40],
        "public_area": bool(location.get("public_area", body.get("public_area"))),
    }


def _route(route, emergency):
    requested = route if route in ("self_help", "direct_report") else "auto"
    if emergency:
        return "direct_report"
    if requested == "self_help":
        return "self_help"
    return "direct_report" if requested == "direct_report" else "self_help"


def build_workflow_result(body):
    """Build the JSON contract without creating a ticket or trusting identity."""
    lang = body.get("lang") if body.get("lang") in ("zh", "en") else "zh"
    complaint = str(body.get("complaint") or body.get("text") or "").strip()[:2000]
    if not complaint:
        raise ValueError("complaint is required")
    images = body.get("image_ids") or []
    if not isinstance(images, list):
        images = []
    location = _clean_location(body)
    user = {"role": "student", "college": location["college"]}
    history = body.get("history")
    if not isinstance(history, list) or not history:
        history = [{"role": "user", "content": complaint}]
    draft, enough, warning = build_draft(history, images, user, lang)
    emergency = bool(warning or draft.get("priority") == "urgent")
    selected_route = _route(str(body.get("route") or "auto"), emergency)
    found = advice_for_student(complaint, user, category=draft.get("repair_type"), lang=lang)
    steps = found.get("steps") or []
    repair_type = draft.get("repair_type") or "other"
    repair = REPAIR_BY_ID.get(repair_type) or REPAIR_BY_ID["other"]
    slots = body.get("slots") if isinstance(body.get("slots"), list) else []
    visit_date = str(body.get("date") or date.today().isoformat())[:10]
    ticket_payload = {
        "lang": lang,
        "repair_type": repair_type,
        "priority": "urgent" if emergency else draft.get("priority", "normal"),
        "description": draft.get("description") or complaint,
        "title": draft.get("title") or repair.get("zh", "宿舍设施报修"),
        "date": visit_date,
        "slots": slots,
        "image_ids": list(dict.fromkeys(str(item) for item in images))[:5],
        **location,
    }
    if selected_route == "self_help" and steps:
        message = ("建议先按知识库中的安全步骤排查；若仍未解决，再生成报修工单。"
                   if lang == "zh" else
                   "Try the safe knowledge-base steps first; create a ticket if the issue remains.")
    elif emergency:
        message = ("识别为紧急情况，请停止使用并联系校园保卫、宿管或物业紧急人员。"
                   if lang == "zh" else
                   "This is urgent. Stop using the area and contact campus emergency or residence staff.")
    else:
        message = ("已整理报修信息，请补充可上门时间后提交。"
                   if lang == "zh" else
                   "The repair details are prepared. Add an availability window before submitting.")
    return {
        "contract_version": 1,
        "ok": True,
        "message": message,
        "route": selected_route,
        "route_reason": "emergency_guard" if emergency else "requested_or_auto",
        "emergency": emergency,
        "confidence": "high" if emergency or enough else "medium",
        "repair_type": repair_type,
        "repair_label": repair.get("zh") if lang == "zh" else repair.get("en"),
        "priority": ticket_payload["priority"],
        "safety_warning": warning,
        "knowledge": {
            "status": found.get("status"),
            "kb_status": found.get("kb_status"),
            "steps": steps if selected_route == "self_help" else [],
        },
        "draft": draft,
        "ticket_payload": ticket_payload,
        "ready_to_submit": bool(ticket_payload["slots"] and enough),
        "created_ticket": False,
    }
