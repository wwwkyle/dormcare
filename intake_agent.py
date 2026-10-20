"""Stateful conversational intake orchestration with a no-model fallback."""
import base64
import json
import os
import time

from agent_prompts import INTAKE_AGENT_PROMPT
from agent_tools import build_draft, validate_draft
from ai_agents import call_model


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


def respond(api_key, model, session, user, lang="zh", store=None, upload_dir=""):
    history = session.get("history") or []
    image_ids = session.get("image_ids") or []
    local_draft, enough, warning = build_draft(history, image_ids, user, lang)
    if not api_key:
        return _fallback(local_draft, enough, warning, lang)

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
