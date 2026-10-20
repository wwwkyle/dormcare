"""Stateful conversational intake orchestration with a no-model fallback."""
import json
import time

from agent_prompts import INTAKE_AGENT_PROMPT
from agent_tools import build_draft, validate_draft
from ai_agents import call_model


def respond(api_key, model, session, user, lang="zh"):
    history = session.get("history") or []
    image_ids = session.get("image_ids") or []
    local_draft, enough, warning = build_draft(history, image_ids, user, lang)
    if not api_key:
        return _fallback(local_draft, enough, warning, lang)

    messages = [{"role": "system", "content": INTAKE_AGENT_PROMPT}]
    messages.extend({"role": x.get("role"), "content": x.get("content", "")}
                    for x in history[-12:])
    for attempt in range(2):
        try:
            raw = call_model(api_key, model, messages, max_tokens=800,
                             temperature=0, json_mode=True)
            data = json.loads(raw)
            draft = data.get("draft")
            if data.get("complete") and not validate_draft(draft):
                raise ValueError("invalid draft")
            data["source"] = "model"
            data["safety_warning"] = data.get("safety_warning") or warning
            return data
        except Exception:
            if attempt == 0:
                time.sleep(0.15)
    result = _fallback(local_draft, enough, warning, lang)
    result["source"] = "fallback_after_model_error"
    return result


def _fallback(draft, enough, warning, lang):
    if enough:
        reply = ("信息已整理为草稿，请核对后确认创建工单。"
                 if lang == "zh" else
                 "I have prepared a draft. Review it before confirming creation.")
    else:
        reply = ("请再描述具体故障现象、发生位置和当前是否仍可使用。"
                 if lang == "zh" else
                 "Please describe the exact symptom, its location, and whether it is still usable.")
    return {"reply": reply, "complete": enough,
            "draft": draft if enough else None,
            "safety_warning": warning, "source": "rules"}
