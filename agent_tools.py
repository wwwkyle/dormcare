"""Deterministic business tools used by the intake agent."""
from datetime import date

from ai_agents import fallback_classify
from config import MAX_TITLE, REPAIR_BY_ID

HIGH_RISK = ("漏电", "触电", "火花", "燃气", "煤气", "冒烟", "起火", "大面积漏水",
             "困住", "被困", "锁在", "无法逃生",
             "electrical leak", "electric shock", "gas leak", "smoke", "fire",
             "sparking", "major flood", "flooding", "trapped", "locked in")

# Sentence / clause punctuation in both languages. A title should end on a
# whole thought; slicing a description at a fixed index cut words in half.
_SENTENCE_END = "。！？；.!?;\n"
_CLAUSE_END = "，,、：:"
_ELLIPSIS = "…"
# Keep collecting sentences until the title actually says something about the
# fault. A bare greeting is dropped, but a short fault ("门锁坏了。") is kept.
_MIN_TITLE_CHARS = 12
_GREETINGS = ("你好", "您好", "hi", "hello", "hey", "请问", "麻烦你", "老师你好")


def _clip(text, limit):
    """Elide at the last clause boundary within budget, never mid-thought."""
    if len(text) <= limit:
        return text
    head = text[:max(1, limit - 1)]
    cut = -1
    for index, char in enumerate(head):
        if char in _CLAUSE_END + _SENTENCE_END + " \t":
            cut = index + 1
    if cut > 0:
        head = head[:cut]
    return head.rstrip(" \t" + _SENTENCE_END + _CLAUSE_END) + _ELLIPSIS


def _sentences(text):
    """Split into sentences, keeping the closing punctuation on each one."""
    parts, buffer = [], []
    for char in text:
        buffer.append(char)
        if char in _SENTENCE_END and "".join(buffer).strip():
            parts.append("".join(buffer).strip())
            buffer = []
    tail = "".join(buffer).strip()
    if tail:
        parts.append(tail)
    return parts


def make_title(text, limit=MAX_TITLE):
    """Turn raw student input into a complete, readable ticket title.

    The old draft generator used ``text.splitlines()[0][:60]``, which handed
    the student a half sentence that merely echoed their description. Build a
    title from whole sentences instead: drop a leading greeting, stop once the
    fault is described, and elide at a clause boundary only for a genuinely
    long sentence. Short titles the student types themselves pass unchanged.
    """
    value = " ".join(str(text or "").split())
    if not value:
        return ""
    if len(value) <= limit:
        return value

    sentences = _sentences(value)
    while len(sentences) > 1:
        opener = sentences[0].rstrip("".join(_SENTENCE_END) + " \t").strip().lower()
        if not opener or any(opener == g or opener.startswith(g + " ")
                             for g in _GREETINGS):
            sentences.pop(0)
        else:
            break

    chosen, used = [], 0
    for index, sentence in enumerate(sentences):
        room = limit - used
        if used and len(sentence) > room:
            break
        chosen.append(_clip(sentence, room) if len(sentence) > room else sentence)
        used += len(chosen[-1])
        if used >= _MIN_TITLE_CHARS or index == len(sentences) - 1:
            break
    title = "".join(chosen).strip()
    return _clip(title, limit) if title else _clip(value, limit)


def build_draft(history, image_ids, user, lang="zh"):
    text = "\n".join(
        str(item.get("fault_text" if item.get("action") else "content") or "")
        for item in history if item.get("role") == "user").strip()
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
    enough = risky or len(text) >= (12 if lang == "zh" else 30)
    draft = {
        "title": make_title(text),
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


def confidence_for(history, draft):
    text = " ".join(
        str(item.get("fault_text" if item.get("action") else "content") or "")
        for item in history if item.get("role") == "user").strip()
    if not text:
        return "low"
    if draft.get("priority") == "urgent" or len(text) >= 24:
        return "high"
    return "medium"


def next_question_for(history, draft, lang="zh"):
    """Return exactly one highest-priority question and safe click choices."""
    text = " ".join(
        str(item.get("fault_text" if item.get("action") else "content") or "")
        for item in history if item.get("role") == "user").strip()
    if len(text) < (12 if lang == "zh" else 30):
        return {
            "field": "phenomenon",
            "text": ("请选择最接近的故障现象，或补充一句具体表现。"
                     if lang == "zh" else
                     "Choose the closest symptom, or briefly describe what happens."),
            "options": ([
                {"value": "not_working", "label": "完全无法使用"},
                {"value": "intermittent", "label": "时好时坏"},
                {"value": "leaking", "label": "漏水 / 渗漏"},
                {"value": "noise", "label": "异响 / 异味"},
            ] if lang == "zh" else [
                {"value": "not_working", "label": "Not working"},
                {"value": "intermittent", "label": "Intermittent"},
                {"value": "leaking", "label": "Leaking"},
                {"value": "noise", "label": "Noise / smell"},
            ]),
        }
    return None


def validate_draft(draft):
    if not isinstance(draft, dict):
        return False
    return (draft.get("repair_type") in REPAIR_BY_ID
            and draft.get("priority") in ("urgent", "high", "normal", "low")
            and bool(str(draft.get("description") or "").strip()))
