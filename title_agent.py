"""Short, complete ticket titles from the configured chat model.

The student's free text is a description, not a title. Echoing its first line
produced clipped, unhelpful labels, so ask the model for a concise summary in
the student's language. This is strictly best-effort: with no key, a failed or
wrong-language reply, the caller falls back to agent_tools.make_title, which
is deterministic and always available.
"""
import json
import re

from ai_agents import AIError, call_model
from config import MAX_TITLE

# The title must be a label, never a restatement of the description. Keeping
# the ceiling well below the storage limit makes titles scan well in lists.
SUMMARY_LIMIT = {"zh": 24, "en": 64}
SUMMARY_WORDS = {"zh": (4, 14), "en": (3, 9)}

_SYSTEM = {
    "zh": """你是宿舍报修系统的标题编辑。把学生描述压缩成一个清晰、直观、精简的工单标题。

规则：
- 只输出一行 JSON：{"title":"..."}，不要解释、不要 Markdown、不要代码块。
- 标题用简体中文，4 到 16 个字，能一眼看出「什么东西出了什么问题」。
- 概括核心故障，不要照抄整段描述，不要写地点、姓名、电话、时间。
- 不要使用引号、句号、感叹号或省略号；不要用「报修」「工单」作前缀。
- 只依据给定描述，不能编造描述里没有的信息。""",
    "en": """You are the title editor for a dorm repair desk. Compress a student's
description into one short, direct ticket title.

Rules:
- Output one line of JSON only: {"title":"..."}. No explanation, no Markdown, no code fence.
- Write in English, 3 to 9 words, so the fault is obvious at a glance.
- Summarize the core problem; do not copy the whole description and do not include
  a location, name, phone number or time.
- No quotes, no trailing full stop or exclamation mark, no "repair" or "ticket" prefix.
- Use only facts present in the description; never invent details.""",
}


def _usable(title, lang):
    """Validate the model title before it can reach a draft or a ticket."""
    if not isinstance(title, str):
        return ""
    value = " ".join(title.split()).strip(" \t\r\n\"'`:;，。,.!?！？…")
    if not value or "\n" in value:
        return ""
    if len(value) > SUMMARY_LIMIT.get(lang, SUMMARY_LIMIT["zh"]):
        return ""
    has_zh = any("\u3400" <= char <= "\u9fff" for char in value)
    if (lang == "zh") != has_zh:
        return ""
    low, high = SUMMARY_WORDS[lang]
    words = value.split() if lang == "en" else list(value)
    if not low <= len(words) <= high:
        return ""
    return value[:MAX_TITLE]


def summarize_title(api_key, model, description, lang="zh"):
    """Return a model-written title, or "" when it cannot be produced safely.

    An empty result means "keep the deterministic fallback"; the caller never
    has to handle a model error or a malformed reply itself.
    """
    if not api_key or not description:
        return ""
    lang = lang if lang in ("zh", "en") else "zh"
    source = " ".join(str(description).split())[:1000]
    if not source:
        return ""
    try:
        raw = call_model(
            api_key, model,
            [{"role": "system", "content": _SYSTEM[lang]},
             {"role": "user", "content": source}],
            max_tokens=120, temperature=0, json_mode=True,
        )
        text = re.sub(r"^```(?:json)?\s*", "", (raw or "").strip())
        text = re.sub(r"\s*```$", "", text).strip()
        data = json.loads(text) if text else {}
        return _usable(data.get("title") if isinstance(data, dict) else "", lang)
    except (AIError, ValueError, TypeError, KeyError):
        return ""
