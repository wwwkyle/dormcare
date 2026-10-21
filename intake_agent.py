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


SAFE_SELF_HELP_TYPES = {
    "desk", "chair", "wardrobe", "network", "curtain", "door",
}

KB_STATUS = "empty"

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
        "kb_status": KB_STATUS,
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


def _advice_turn(draft, lang, text="", emergency=False):
    """Conservative, repeatable advice for every turn in the advice route."""
    low = (text or "").lower()
    unresolved = any(word in low for word in UNRESOLVED_WORDS)
    if emergency:
        tips = ([
            "立即停止使用并远离故障点，不要触碰、拆卸或尝试维修。",
            "仅在无需靠近故障点且能安全操作时关闭总电源或总阀；有烟火、燃气味或人员危险时立即撤离。",
            "联系校园保卫、宿管或物业紧急人员；如需记录和跟进，请点击“生成工单”。",
        ] if lang == "zh" else [
            "Stop using the area and keep away; do not touch, open, dismantle, or repair anything.",
            "Only if safely reachable away from the fault, cut main power or close the main valve; evacuate for smoke, fire, gas, or danger to people.",
            "Contact campus security, residence staff, or emergency property staff; use Create ticket if tracking is needed.",
        ])
        reply = tips[0]
        source = "safety_fallback"
    else:
        if draft.get("repair_type") not in SAFE_SELF_HELP_TYPES:
            return None
        tips = list(SELF_HELP.get(draft.get("repair_type"), {}).get(lang, []))[:3]
        if not tips:
            return None
        if unresolved:
            reply = ("既然仍未恢复，请停止继续尝试并等待师傅处理。可点击“生成工单”，现有信息和图片会保留。"
                     if lang == "zh" else
                     "Since it is still unresolved, stop further attempts and wait for a technician. Use Create ticket; your details and images will be kept.")
        elif any(word in low for word in ("安全吗", "安全么", "safe", "dangerous")):
            reply = ("只进行下列不拆机、不接触带电部件的步骤；如现场异常、步骤不明确或你感到不安全，请立即停止并等待师傅。"
                     if lang == "zh" else
                     "Only use the non-invasive steps below. Stop immediately and wait for a technician if anything looks abnormal, is unclear, or feels unsafe.")
        else:
            reply = ("可以继续按以下保守步骤排查；不要拆机或接触带电部件，不确定时请停止并等待师傅。"
                     if lang == "zh" else
                     "Continue only with these conservative checks. Do not open equipment or touch live parts; stop and wait for a technician if unsure.")
        source = "fallback"
    return {
        "reply": reply, "stage": "advice", "state": "advice",
        "complete": False, "draft": draft,
        "detected": {"repair_type": draft.get("repair_type"),
                     "priority": draft.get("priority")},
        "missing": [], "self_help": tips,
        "safety_warning": (tips[0] if emergency else ""),
        "source": "rules",
        "solution": {"source": source, "safety_reviewed": True,
                     "steps": [{"text": tip, "image_url": None} for tip in tips]},
        "route_options": [{"id": "smart_repair", "enabled": True,
                           "recommended": bool(emergency or unresolved)}],
        "active_route": "self_help",
    }


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
        advice = _advice_turn(local_draft, lang,
                              str(history[-1].get("content") or "") if history else "",
                              bool(warning or local_draft.get("priority") == "urgent"))
        if advice:
            return _enrich(advice, session, local_draft, lang)
        result = _fallback(local_draft, enough, warning, lang, force_draft)
        result.update({
            "reply": ("知识库当前为空，暂无合适方案。请停止自行处理并点击“生成工单”，现有信息和图片会保留。"
                      if lang == "zh" else
                      "No suitable knowledge-base guidance is available. Stop attempting repairs and use Create ticket; your details and images will be kept."),
            "stage": "no_solution", "state": "no_solution", "complete": False,
            "solution": None, "self_help": [], "missing": [],
            "next_question": None, "question_options": [],
            "route_options": [{"id": "smart_repair", "enabled": True,
                               "recommended": True}],
            "active_route": "self_help",
        })
        return _enrich(result, session, local_draft, lang)
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
