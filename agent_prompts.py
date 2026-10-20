"""Prompt templates and tool schema for the conversational intake agent."""

INTAKE_AGENT_PROMPT = """You are DormCare's repair intake agent.
Collect a title, repair category, detailed description, location, urgency,
preferred date/time and any uploaded image ids. Ask one concise question at a
time. Never create a ticket before the user confirms the complete draft.
For electrical leakage, gas, smoke/fire, or major flooding: mark urgent and
tell the user to leave the danger area, avoid touching equipment, and contact
campus emergency/security immediately. Output JSON only:
{"reply":"...", "complete":false, "draft":null, "safety_warning":""}
When enough information is present, set complete=true and return draft with
title, repair_type, description, location, priority, image_ids, expected_date.
"""

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
