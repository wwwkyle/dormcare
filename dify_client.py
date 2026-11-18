"""Server-side Dify Chat API client using only the Python standard library."""
import hashlib
import http.client
import json
import os
import secrets
import urllib.error
import urllib.request
from urllib.parse import urlsplit

import dify_config
from upload_service import MAGIC, MAX_IMAGE_BYTES

MAX_IMAGES = 3


class DifyError(Exception):
    """A configuration, transport or response error safe to handle locally."""


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, response, code, message, headers, new_url):
        return None


def enabled():
    return dify_config.enabled()


def action_text(action, lang="zh"):
    messages = {
        "create_draft": (
            "问题仍未解决，仍需师傅上门，请生成报修工单草稿。",
            "The problem is still unresolved. I need a technician; please create a repair ticket draft."),
        "smart_repair": (
            "我想直接报修，请安排师傅上门并生成报修工单草稿。",
            "I want to request a repair directly. Please arrange a technician and prepare a ticket draft."),
        "self_help": (
            "我想先尝试安全的自助排查。",
            "I would like to try safe troubleshooting first."),
        "resolved": (
            "问题已经解决，无需师傅上门，不需要生成工单。",
            "The problem is resolved. I do not need a technician or a repair ticket."),
    }
    message = messages.get(action)
    return message[0 if lang == "zh" else 1] if message else ""


def _request_json(base, path, data, content_type, key, timeout):
    try:
        request = urllib.request.Request(
            base + path, data=data,
            headers={"Authorization": "Bearer " + key,
                     "Content-Type": content_type, "Accept": "application/json"},
            method="POST")
        with urllib.request.build_opener(_NoRedirect()).open(request, timeout=timeout) as response:
            raw = response.read(1024 * 1024 + 1)
        if len(raw) > 1024 * 1024:
            raise ValueError("response too large")
        result = json.loads(raw)
        if not isinstance(result, dict) or result.get("event") == "error":
            raise ValueError("invalid response")
        return result
    except urllib.error.HTTPError as exc:
        raise DifyError("http_%d" % exc.code) from None
    except (OSError, ValueError, urllib.error.URLError, http.client.HTTPException):
        raise DifyError("request_failed") from None


def _image_files(session, user, store, upload_dir, base, key, timeout):
    image_ids = session.get("image_ids") or []
    if not isinstance(image_ids, list) or len(image_ids) > MAX_IMAGES:
        raise DifyError("too_many_images")
    scope = hashlib.sha256((base + "\n" + key + "\n" + str(user["id"])).encode()).hexdigest()
    saved = session.get("dify_files") if session.get("dify_files_scope") == scope else {}
    saved = saved if isinstance(saved, dict) else {}
    uploaded, files = {}, []
    for image_id in dict.fromkeys(image_ids):
        record = store.upload(image_id) if store and upload_dir else None
        if (not record or record.get("owner_user_id") != user["id"]
                or record.get("ticket_id") or record.get("purpose") not in ("agent", "ticket")
                or record.get("mime") not in MAGIC):
            raise DifyError("invalid_attachment")
        filename = os.path.basename(record.get("filename") or "")
        path = os.path.realpath(os.path.join(upload_dir, filename))
        try:
            if os.path.commonpath((os.path.realpath(upload_dir), path)) != os.path.realpath(upload_dir):
                raise ValueError("invalid path")
            with open(path, "rb") as source:
                image = source.read(MAX_IMAGE_BYTES + 1)
            if (not image or len(image) > MAX_IMAGE_BYTES
                    or not image.startswith(MAGIC[record["mime"]][0])):
                raise ValueError("invalid image")
        except (OSError, ValueError):
            raise DifyError("invalid_attachment") from None
        file_id = saved.get(image_id)
        if not isinstance(file_id, str) or not file_id.strip():
            boundary = "dormcare-" + secrets.token_hex(24)
            name = "image" + MAGIC[record["mime"]][1]
            body = (
                "--" + boundary + '\r\nContent-Disposition: form-data; name="user"\r\n\r\n'
                + "dormcare-" + str(user["id"]) + "\r\n--" + boundary
                + '\r\nContent-Disposition: form-data; name="file"; filename="' + name
                + '"\r\nContent-Type: ' + record["mime"] + "\r\n\r\n"
            ).encode() + image + ("\r\n--" + boundary + "--\r\n").encode()
            response = _request_json(base, "/files/upload", body,
                                     "multipart/form-data; boundary=" + boundary, key, timeout)
            file_id = response.get("id")
            if not isinstance(file_id, str) or not file_id.strip():
                raise DifyError("invalid_upload_response")
        uploaded[image_id] = file_id
        files.append({"type": "image", "transfer_method": "local_file", "upload_file_id": file_id})
    return files, uploaded, scope


def chat(session, user, lang="zh", force_draft=False, advice_mode=False, store=None, upload_dir=""):
    """Send a blocking chat turn; never expose credentials in returned errors."""
    key = dify_config.key()
    base = dify_config.base_url()
    try:
        address = urlsplit(base)
        local = address.hostname in ("localhost", "127.0.0.1", "::1")
        if (not key or not address.hostname or address.username or address.password
                or address.query or address.fragment
                or (address.scheme != "https" and not (local and address.scheme == "http"))):
            raise ValueError("invalid endpoint")
        timeout = float(os.environ.get("DIFY_TIMEOUT_SECONDS", "120"))
        if not 0 < timeout <= 300:
            raise ValueError("invalid timeout")
        extra_inputs = json.loads(os.environ.get("DIFY_INPUTS_JSON", "{}"))
        if not isinstance(extra_inputs, dict):
            raise ValueError("inputs must be an object")
    except (TypeError, ValueError):
        raise DifyError("invalid_configuration") from None

    files, uploaded, scope = _image_files(session, user, store, upload_dir, base, key, timeout)
    history = session.get("history") or []
    turns = [item for item in history if item.get("role") == "user"]
    complaint = "\n".join(str(item.get("content") or "") for item in turns)[-2000:]
    latest = turns[-1] if turns else {}
    action = latest.get("action") or ""
    saved_draft = session.get("draft") or {}
    route = ("direct_report" if force_draft else
             ("self_help" if action == "self_help" else "auto"))
    inputs = dict(extra_inputs)
    inputs.update({
        "complaint": complaint, "route": route, "lang": lang,
        "resolved_after_check": ("yes" if action == "resolved" else
                                 ("no" if force_draft else "unknown")),
        "location_json": json.dumps({
            name: user.get(name, "") for name in ("college", "block", "dorm")
        }, ensure_ascii=False),
        "date": saved_draft.get("expected_date") or "",
        "slots_json": json.dumps(saved_draft.get("slots") or []),
    })
    conversation_id = ((session.get("dify_conversation_id") or "")
                       if session.get("dify_lang") == lang else "")
    instruction = ("请用中文回复。" if lang == "zh" else "Reply in English. ")
    instruction += ("工单须由学生确认后提交，目前尚未提交工单。" if lang == "zh" else
                    "No ticket has been submitted; submission requires student confirmation. ")
    current = str(latest.get("content") or "") if conversation_id else complaint
    if force_draft and action not in ("create_draft", "smart_repair"):
        current += "\n" + action_text("create_draft", lang)
    if not current.strip():
        current = ("请查看本轮上传的图片。" if lang == "zh" else "Please look at the attached images.")
    query = instruction + "\n" + current
    payload = {
        "inputs": inputs, "query": query, "response_mode": "blocking",
        "conversation_id": conversation_id, "user": "dormcare-" + str(user["id"]),
        "auto_generate_name": False,
    }
    if files:
        payload["files"] = files
    result = _request_json(base, "/chat-messages", json.dumps(payload).encode("utf-8"),
                           "application/json", key, timeout)
    if (not isinstance(result.get("answer"), str) or not result["answer"].strip()
            or not isinstance(result.get("conversation_id"), str)
            or not result["conversation_id"].strip()):
        raise DifyError("invalid_response")
    if files or session.get("dify_files"):
        result.update({"dify_files": uploaded, "dify_files_scope": scope})
    return result
