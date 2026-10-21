"""Stateful conversational intake orchestration with a no-model fallback."""
import base64
import json
import os
import time

from agent_prompts import INTAKE_AGENT_PROMPT
from agent_tools import (build_draft, confidence_for, next_question_for,
                         validate_draft)
from ai_agents import SELF_HELP, call_model


SAFE_SELF_HELP_TYPES = {
    "desk", "chair", "wardrobe", "network", "curtain", "door",
}

KB_STATUS = "empty"


def _enrich(result, session, draft, lang):
    """Add the stable v2 contract while preserving every legacy field."""
    emergency = bool(result.get("safety_warning") or
                     draft.get("priority") == "urgent")
    question = None if emergency else next_question_for(
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
        "route_options": ([{
            "id": "smart_repair", "enabled": True, "recommended": True,
        }] if emergency else [
            {"id": "self_help", "enabled": True,
             "recommended": result.get("stage") == "advice"},
            {"id": "smart_repair", "enabled": True,
             "recommended": result.get("stage") != "advice"},
        ]),
        "solution": ({
            "source": "fallback", "safety_reviewed": True, "steps": steps,
        } if steps else None),
        "next_question": question,
        "question_options": (question.get("options", []) if question else []),
        "state": result.get("stage", "follow_up"),
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
            force_draft=False):
    history = session.get("history") or []
    image_ids = session.get("image_ids") or []
    local_draft, enough, warning = build_draft(history, image_ids, user, lang)
    if force_draft:
        return _enrich(
            _fallback(local_draft, enough, warning, lang, True),
            session, local_draft, lang)
    if not api_key:
        return _enrich(
            _fallback(local_draft, enough, warning, lang, force_draft),
            session, local_draft, lang)

    messages = [{"role": "system", "content": INTAKE_AGENT_PROMPT}]
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
