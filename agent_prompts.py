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


ADVICE_PROMPT = """You are DormCare's repair advisor.
Write a practical repair suggestion for the student's latest message.
The required language is {language}. Do not mix Chinese and English.
Use the knowledge notes when they describe this fault, and put the actual steps in the reply.
The student only sees the reply. Do not mention a category, priority, confidence, or a knowledge-base label.
Output JSON only: {{"reply":"..."}}
Knowledge notes:
{notes}
"""


def advice_prompt(lang, notes):
    """Ask the model for the only text the student should read."""
    language = "Simplified Chinese" if lang == "zh" else "English"
    return ADVICE_PROMPT.format(language=language, notes=notes or "(none)")

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
