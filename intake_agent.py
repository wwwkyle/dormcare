"""Stateful conversational intake orchestration with a no-model fallback."""
import base64
import json
import os
import re
import time

from agent_prompts import intake_prompt
from agent_tools import (build_draft, confidence_for, next_question_for,
                         validate_draft)
from ai_agents import SELF_HELP, call_model
from knowledge_base import advice_for_student


SAFE_SELF_HELP_TYPES = {
    "desk", "chair", "wardrobe", "network", "curtain", "door",
}

KB_STATUS = "ready"

UNRESOLVED_WORDS = (
    "未解决", "没恢复", "没有恢复", "还是不行", "仍然", "无效",
    "not fixed", "not resolved", "still", "didn't work", "doesn't work",
)


def _lang_ok(text, lang):
    """Reject clearly wrong-language model prose, allowing IDs/numbers."""
    value = str(text or "")
    has_zh = bool(re.search(r"[\u3400-\u9fff]", value))
    return has_zh if lang == "zh" else not has_zh


def _result_lang_ok(data, lang):
    fields = [data.get("reply"), data.get("safety_warning")]
    fields.extend(data.get("missing") or [])
    return all(not value or _lang_ok(value, lang) for value in fields)


def _enrich(result, session, draft, lang):
    """Add the stable v2 contract while preserving every legacy field."""
    emergency = bool(result.get("safety_warning") or
                     draft.get("priority") == "urgent")
    advice_state = result.get("stage") in ("advice", "no_solution")
    question = None if emergency or advice_state else next_question_for(
        session.get("history") or [], draft, lang)
    if result.get("stage") == "follow_up" and question:
        result["missing"] = [question["text"]]
    elif result.get("stage") == "follow_up":
        result["missing"] = (result.get("missing") or [])[:1]
    steps = [{"text": tip, "image_url": None}
             for tip in (result.get("self_help") or [])]
    result.update({
        "contract_version": 2,
        "emergency": emergency,
        "confidence": confidence_for(session.get("history") or [], draft),
        "kb_status": result.get("kb_status") or KB_STATUS,
        "route_options": result.get("route_options") or (
            [{"id": "smart_repair", "enabled": True, "recommended": True}]
            if advice_state else [
                {"id": "self_help", "enabled": True, "recommended": False},
                {"id": "smart_repair", "enabled": True,
                 "recommended": emergency},
            ]),
        "solution": result.get("solution") or ({
            "source": "fallback", "safety_reviewed": True, "steps": steps,
        } if steps else None),
        "next_question": question,
        "question_options": (question.get("options", []) if question else []),
        "state": result.get("state", result.get("stage", "follow_up")),
        "response_mode": "complete",
    })
    # An in-progress server draft is recoverable even before it is confirmable.
    if result.get("draft") is None and result.get("stage") in ("follow_up", "advice"):
        result["draft"] = draft
    return result


def _questions(lang):
    if lang == "zh":
        return ["具体出现了什么现象？", "从什么时候开始，当前还能否使用？"]
    return ["What exactly is happening?", "When did it start, and is it still usable?"]


def _advice(draft, lang):
    tips = list(SELF_HELP.get(draft.get("repair_type"), {}).get(lang, []))[:3]
    reply = ("这看起来是低风险问题，可以先尝试以下安全操作。操作后请选择是否仍需报修。"
             if lang == "zh" else
             "This appears low risk. Try these safe steps, then choose whether you still need a repair.")
    return {
        "reply": reply, "stage": "advice", "complete": False, "draft": None,
        "detected": {
            "repair_type": draft.get("repair_type"),
            "priority": draft.get("priority"),
        },
        "missing": [], "self_help": tips, "safety_warning": "", "source": "rules",
    }


_TICKET_INTENT = re.compile(
    r"生成工单|创建工单|提交工单|我要报修|转为报修|帮我报修|"
    r"create a ticket|file a ticket|submit a ticket",
    re.I)
_ADVICE_INTENT = re.compile(
    r"怎么修|如何修|怎样修|怎么处理|如何处理|怎么办|维修建议|自己修|如何维修|怎么维修|"
    r"how to (?:fix|repair)|how do i (?:fix|repair)|repair advice|what should i do",
    re.I)


def student_wants_ticket(text):
    return bool(_TICKET_INTENT.search(text or ""))


def student_wants_advice(text):
    return bool(_ADVICE_INTENT.search(text or ""))


def _retrieval_query(history, text):
    """The latest utterance, plus the previous fault when this turn is a short follow-up."""
    latest = (text or "").strip()
    prior = []
    for item in history or []:
        if item.get("role") != "user":
            continue
        content = str(item.get("content") or "").strip()
        if not content:
            continue
        if content == latest:
            continue
        prior.append(content)
    if not latest and prior:
        latest = prior.pop()
    follow = (len(latest) < 18 or bool(re.search(
        r"这个|那个|还是|哪里|安全|按钮|怎么|如何|where|safe|still|button", latest, re.I)))
    if follow and prior:
        return (prior[-1] + " " + latest)[:2000]
    return latest[:2000]


def _no_solution(draft, lang, message, kb_status):
    return {
        "reply": message, "stage": "no_solution", "state": "no_solution",
        "complete": False, "draft": draft,
        "detected": {"repair_type": draft.get("repair_type"),
                     "priority": draft.get("priority")},
        "missing": [], "self_help": [], "safety_warning": "",
        "source": "knowledge", "kb_status": kb_status, "solution": None,
        "route_options": [{"id": "smart_repair", "enabled": True, "recommended": True}],
        "active_route": "self_help",
    }


def _advice_turn(draft, lang, text="", emergency=False, user=None, history=None):
    """Advice grounded in the dorm knowledge base, with a conservative fallback."""
    low = (text or "").lower()
    unresolved = any(word in low for word in UNRESOLVED_WORDS)
    query = _retrieval_query(history, text)
    found = advice_for_student(
        query or (draft.get("description") or ""), user or {"role": "student"},
        category=draft.get("repair_type"), lang=lang) if query or draft.get("description") else {
            "status": "insufficient_evidence", "kb_status": "no_hit",
            "message": "", "steps": [],
        }
    kb_urgent = found.get("status") in ("urgent_gas", "urgent_manual")
    if kb_urgent or emergency:
        generic = ([
            "立即停止使用并远离故障点，不要触碰、拆卸或尝试维修。",
            "仅在无需靠近故障点且能安全操作时关闭总电源或总阀；有烟火、燃气味或人员危险时立即撤离。",
            "联系校园保卫、宿管或物业紧急人员；如需记录和跟进，请点击“生成工单”。",
        ] if lang == "zh" else [
            "Stop using the area and keep away; do not touch, open, dismantle, or repair anything.",
            "Only if safely reachable away from the fault, cut main power or close the main valve; evacuate for smoke, fire, gas, or danger to people.",
            "Contact campus security, residence staff, or emergency property staff; use Create ticket if tracking is needed.",
        ])
        tips = generic
        reply = (found.get("message") if kb_urgent else "") or tips[0]
        if lang == "en" and re.search(r"[\u3400-\u9fff]", reply):
            reply = tips[0]
        return {
            "reply": reply, "stage": "advice", "state": "advice",
            "complete": False, "draft": draft,
            "detected": {"repair_type": draft.get("repair_type"),
                         "priority": draft.get("priority")},
            "missing": [], "self_help": tips,
            "safety_warning": tips[0],
            "source": "rules",
            "kb_status": found.get("kb_status") or "hit",
            "solution": {"source": "safety_fallback",
                         "safety_reviewed": True,
                         "steps": [{"text": tip, "image_url": None} for tip in tips]},
            "route_options": [{"id": "smart_repair", "enabled": True, "recommended": True}],
            "active_route": "self_help",
        }
    steps = found.get("steps") or []
    if steps:
        tips = [step["text"] for step in steps]
        if unresolved:
            reply = ("既然仍未恢复，请停止继续尝试并生成工单。下面是知识库里已有的保守提示，不要再扩大操作。"
                     if lang == "zh" else
                     "It is still unresolved, so stop and create a ticket. The notes below are only the conservative checks already in the knowledge base.")
        elif any(word in low for word in ("安全吗", "安全么", "safe", "dangerous")):
            reply = ("只参考知识库里这些不拆机、不接触带电或燃气部件的内容。现场不对、或你觉得不安全，就停止并生成工单。"
                     if lang == "zh" else
                     "Use only these knowledge-base checks. Do not open equipment or touch live or gas parts. Stop and create a ticket if anything feels unsafe.")
        else:
            reply = ("以下来自宿舍知识库（资料整理者收集，尚未现场审核，不是确诊）。只做不拆机的步骤；不确定就停止并生成工单。"
                     if lang == "zh" else
                     "These checks come from the dorm knowledge base. They are not a field-approved diagnosis. Do not open equipment; stop and create a ticket if unsure.")
        return {
            "reply": reply, "stage": "advice", "state": "advice",
            "complete": False, "draft": draft,
            "detected": {"repair_type": draft.get("repair_type"),
                         "priority": draft.get("priority")},
            "missing": [], "self_help": tips, "safety_warning": "",
            "source": "knowledge", "kb_status": "hit",
            "solution": {"source": "knowledge", "safety_reviewed": False,
                         "chunk_ids": found.get("chunk_ids") or [],
                         "steps": [{"text": step["text"], "image_url": None,
                                    "chunk_id": step.get("chunk_id")} for step in steps]},
            "route_options": [{"id": "smart_repair", "enabled": True,
                               "recommended": unresolved}],
            "active_route": "self_help",
        }
    if (draft.get("repair_type") in SAFE_SELF_HELP_TYPES and
            found.get("status") in ("insufficient_evidence", "no_procedure", "no_hit", "out_of_scope")):
        tips = list(SELF_HELP.get(draft.get("repair_type"), {}).get(lang, []))[:3]
        if tips and found.get("status") != "no_procedure":
            reply = ("知识库没有更贴切的条目，只能先给你这些不拆机的常规检查。不确定就停止并生成工单。"
                     if lang == "zh" else
                     "Nothing closer is in the knowledge base. These are only ordinary non-invasive checks. Stop and create a ticket if unsure.")
            return {
                "reply": reply, "stage": "advice", "state": "advice",
                "complete": False, "draft": draft,
                "detected": {"repair_type": draft.get("repair_type"),
                             "priority": draft.get("priority")},
                "missing": [], "self_help": tips, "safety_warning": "",
                "source": "rules", "kb_status": found.get("kb_status") or "no_hit",
                "solution": {"source": "fallback", "safety_reviewed": True,
                             "steps": [{"text": tip, "image_url": None} for tip in tips]},
                "route_options": [{"id": "smart_repair", "enabled": True,
                                   "recommended": unresolved}],
                "active_route": "self_help",
            }
    message = found.get("message") or (
        "知识库里没有合适的自行处理步骤。请停止操作并生成工单，现有信息和图片会保留。"
        if lang == "zh" else
        "No suitable do-it-yourself steps are in the knowledge base. Stop and create a ticket; your details and images will be kept.")
    if lang == "en" and re.search(r"[\u3400-\u9fff]", message):
        message = ("No suitable do-it-yourself steps are in the knowledge base. "
                   "Stop and create a ticket; your details and images will be kept.")
    return _no_solution(draft, lang, message, found.get("kb_status") or "no_hit")


def _model_messages(history, store, upload_dir):
    messages = []
    for item in history[-12:]:
        role = item.get("role")
        text = item.get("content", "")
        image_ids = item.get("image_ids") or []
        if role != "user" or not image_ids or not store or not upload_dir:
            messages.append({"role": role, "content": text})
            continue
        content = [{"type": "text", "text": text}]
        for image_id in image_ids:
            upload = store.upload(image_id)
            if not upload:
                continue
            path = os.path.join(upload_dir, os.path.basename(upload.get("filename", "")))
            try:
                with open(path, "rb") as fh:
                    encoded = base64.b64encode(fh.read()).decode("ascii")
            except OSError:
                continue
            content.append({
                "type": "image_url",
                "image_url": {
                    "url": "data:%s;base64,%s" % (upload.get("mime"), encoded),
                    "detail": "low",
                },
            })
        messages.append({"role": role, "content": content if len(content) > 1 else text})
    return messages


def respond(api_key, model, session, user, lang="zh", store=None, upload_dir="",
            force_draft=False, advice_mode=False):
    history = session.get("history") or []
    image_ids = session.get("image_ids") or []
    local_draft, enough, warning = build_draft(history, image_ids, user, lang)
    if advice_mode or (session.get("active_route") == "self_help" and not force_draft):
        latest = ""
        for item in reversed(history):
            if item.get("role") == "user":
                latest = str(item.get("content") or "")
                break
        advice = _advice_turn(
            local_draft, lang, latest,
            bool(warning or local_draft.get("priority") == "urgent"),
            user=user, history=history)
        return _enrich(advice, session, local_draft, lang)
    if force_draft:
        return _enrich(
            _fallback(local_draft, enough, warning, lang, True),
            session, local_draft, lang)
    if not api_key:
        return _enrich(
            _fallback(local_draft, enough, warning, lang, force_draft),
            session, local_draft, lang)

    messages = [{"role": "system", "content": intake_prompt(lang)}]
    messages.extend(_model_messages(history, store, upload_dir))
    for attempt in range(2):
        try:
            raw = call_model(api_key, model, messages, max_tokens=800,
                             temperature=0, json_mode=True)
            data = json.loads(raw)
            draft = data.get("draft")
            if isinstance(draft, dict):
                # Upload IDs are server-owned references, never values the model
                # is allowed to invent or discard.
                draft["image_ids"] = list(image_ids)
            if data.get("complete") and not validate_draft(draft):
                raise ValueError("invalid draft")
            if data.get("complete"):
                if (not force_draft and not warning and
                        draft.get("repair_type") in SAFE_SELF_HELP_TYPES and
                        draft.get("priority") in ("normal", "low")):
                    result = _advice(draft, lang)
                    result["source"] = "model"
                    return _enrich(result, session, draft, lang)
                data.update({
                    "stage": "draft", "complete": True,
                    "detected": {
                        "repair_type": draft.get("repair_type"),
                        "priority": draft.get("priority"),
                    },
                    "missing": [], "self_help": [],
                })
            else:
                data.update({
                    "stage": "follow_up", "complete": False, "draft": None,
                    "detected": data.get("detected") or {
                        "repair_type": local_draft.get("repair_type"),
                        "priority": local_draft.get("priority"),
                    },
                    "missing": data.get("missing") or _questions(lang),
                    "self_help": [],
                })
            data["source"] = "model"
            data["safety_warning"] = data.get("safety_warning") or warning
            if not _result_lang_ok(data, lang):
                raise ValueError("model replied in the wrong language")
            return _enrich(data, session, draft or local_draft, lang)
        except Exception:
            if attempt == 0:
                time.sleep(0.15)
    result = _fallback(local_draft, enough, warning, lang, force_draft)
    result["source"] = "fallback_after_model_error"
    return _enrich(result, session, local_draft, lang)


def _fallback(draft, enough, warning, lang, force_draft=False):
    detected = {
        "repair_type": draft.get("repair_type"),
        "priority": draft.get("priority"),
    }
    if enough:
        if (not force_draft and not warning
                and draft.get("repair_type") in SAFE_SELF_HELP_TYPES
                and draft.get("priority") in ("normal", "low")):
            return _advice(draft, lang)
        reply = ("信息已整理为工单草稿，请核对和补充上门时间后确认提交。"
                 if lang == "zh" else
                 "Your ticket draft is ready. Review it, add a visit time, and confirm.")
        return {
            "reply": reply, "stage": "draft", "complete": True, "draft": draft,
            "detected": detected, "missing": [], "self_help": [],
            "safety_warning": warning, "source": "rules",
        }
    reply = ("我还需要两项关键信息，请在下方继续回答。"
             if lang == "zh" else
             "I still need two key details. Please answer below.")
    return {
        "reply": reply, "stage": "follow_up", "complete": False, "draft": None,
        "detected": detected, "missing": _questions(lang), "self_help": [],
        "safety_warning": warning, "source": "rules",
    }
