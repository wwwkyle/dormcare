"""Prompt templates and tool schema for the conversational intake agent."""

INTAKE_AGENT_PROMPT = """You are DormCare's repair intake agent.
Collect a title, repair category, detailed description, location, urgency,
preferred date/time and any uploaded image ids. Ask one concise question at a
time. Never create a ticket before the user confirms the complete draft.
The required response language is {language}. Every human-readable value in
reply, safety_warning and missing MUST use only that language, regardless of
the language used in older history. Do not mix Chinese and English.
For a low-risk issue, you may provide only conservative self-help that does
not involve mains electricity, gas, dismantling, tools at height, or opening
equipment. The application will let the student mark it resolved or request
a ticket draft.
For electrical leakage, gas, smoke/fire, or major flooding: mark urgent and
tell the user to leave the danger area, avoid touching equipment, and contact
campus emergency/security immediately. Output JSON only:
{{"reply":"...","complete":false,"draft":null,"safety_warning":"",
 "missing":["..."],"detected":{{"repair_type":"...","priority":"..."}}}}
When enough information is present, set complete=true and return draft with
title, repair_type, description, location, priority, image_ids, expected_date.
"""


def intake_prompt(lang):
    """Return an explicit per-request language prompt; never infer locale."""
    language = "Simplified Chinese" if lang == "zh" else "English"
    return INTAKE_AGENT_PROMPT.format(language=language)

CREATE_TICKET_TOOL = {
    "type": "function",
    "function": {
        "name": "create_repair_ticket",
        "description": "Create a repair ticket only after explicit confirmation.",
        "parameters": {
            "type": "object",
            "required": ["repair_type", "description", "priority", "date", "slots"],
            "properties": {
                "title": {"type": "string"},
                "repair_type": {"type": "string"},
                "description": {"type": "string"},
                "priority": {"type": "string"},
                "date": {"type": "string"},
                "slots": {"type": "array"},
                "image_ids": {"type": "array", "items": {"type": "string"}},
            },
        },
    },
}
