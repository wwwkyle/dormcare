"""Focused upload, completion-proof and intake-agent tests."""
import base64
import os
import re
import sys
import time
from pathlib import Path

from testclient import (DEMO_STUDENT, DEMO_STUDENT_2, DEMO_WORKERS, Client,
                        reset_store, server_output, spawn_server, store_path)

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))
PORT = 3118
BASE = "http://127.0.0.1:%d" % PORT
PNG = b"\x89PNG\r\n\x1a\n" + b"\0" * 24


def ok(label, condition):
    print(("PASS  " if condition else "FAIL  ") + label)
    if not condition:
        raise AssertionError(label)


def has_zh(value):
    return bool(re.search(r"[\u3400-\u9fff]", str(value or "")))


def main():
    data = reset_store(store_path(HERE, "multimodal"))
    upload_dir = str(HERE / "data" / "test-uploads")
    proc, log_path = spawn_server(
        HERE, PORT, "", data=data, log_name="test_multimodal.log",
        env_extra={"DORMCARE_UPLOAD_DIR": upload_dir})
    try:
        probe = Client(BASE)
        for _ in range(40):
            time.sleep(.25)
            if probe.call("/api/health")[0] == 200:
                break
        stu = Client(BASE, *DEMO_STUDENT)
        other = Client(BASE, *DEMO_STUDENT_2)
        cfg = stu.call("/api/config")[1]
        day = cfg["days"][0]
        encoded = base64.b64encode(PNG).decode()

        s, up = stu.call("/api/uploads", {
            "purpose": "ticket", "images": [{"mime": "image/png", "data": encoded}]})
        image_id = up["uploads"][0]["id"]
        ok("legal PNG upload", s == 200 and image_id)
        ok("owner can read upload", stu.call("/api/uploads/" + image_id)[0] == 200)
        ok("other student cannot read upload", other.call("/api/uploads/" + image_id)[0] == 403)

        s, bad = stu.call("/api/uploads", {
            "images": [{"mime": "image/jpeg", "data": encoded}]})
        ok("forged MIME rejected", s == 400 and bad.get("code") == "image_signature_mismatch")
        huge = b"\x89PNG\r\n\x1a\n" + b"x" * (5 * 1024 * 1024)
        s, bad = stu.call("/api/uploads", {
            "images": [{"mime": "image/png", "data": base64.b64encode(huge).decode()}]})
        ok("decoded oversize rejected", s == 400 and bad.get("code") == "image_too_large")

        s, made = stu.call("/api/tickets", {
            "repair_type": "light", "priority": "normal", "date": day,
            "slots": [[1, 12]], "description": "灯具闪烁且开关发热",
            "image_ids": [image_id]})
        ticket = made["ticket"]
        ok("ticket binds report image", s == 200 and ticket["image_ids"] == [image_id])
        assigned = ticket["assigned_worker"]["worker_id"]
        worker_creds = next(x for x in DEMO_WORKERS if x[0] == assigned)
        worker = Client(BASE, *worker_creds)
        ok("assigned worker can read report image",
           worker.call("/api/uploads/" + image_id)[0] == 200)
        worker.call("/api/tickets/status", {"id": ticket["id"], "status": "accepted"})
        s, refused = worker.call("/api/tickets/status", {"id": ticket["id"], "status": "done"})
        ok("completion without proof rejected",
           s == 400 and refused.get("code") == "completion_images_required")
        s, done_up = worker.call("/api/uploads", {
            "purpose": "completion", "images": [{"mime": "image/png", "data": encoded}]})
        done_id = done_up["uploads"][0]["id"]
        s, completed = worker.call("/api/tickets/status", {
            "id": ticket["id"], "status": "done", "completion_image_ids": [done_id]})
        ok("completion with proof succeeds", s == 200 and
           completed["ticket"]["completion_image_ids"] == [done_id])
        visible = stu.call("/api/tickets")[1]["tickets"][0]
        ok("student sees completion fields", visible.get("completed_at") and
           visible.get("completed_by") and done_id in visible.get("completion_image_ids", []))
        s, english_default = stu.call("/api/tickets", {
            "lang": "en", "repair_type": "chair", "priority": "normal",
            "date": day, "slots": [[1, 4]], "description": "Chair is unstable."})
        ok("manual ticket gets English surname and account-phone defaults",
           s == 200 and english_default["ticket"].get("student_name") == "Student Wang"
           and english_default["ticket"].get("contact") == "13800000001")

        s, agent_up = stu.call("/api/uploads", {
            "purpose": "agent", "images": [{"mime": "image/png", "data": encoded}]})
        agent_image_id = agent_up["uploads"][0]["id"]
        ok("agent image upload succeeds", s == 200 and agent_image_id)

        s, agent = stu.call("/api/agent/session", {"lang": "zh"})
        aid = agent["session"]["id"]
        s, first = stu.call("/api/agent/message", {
            "id": aid, "lang": "zh", "text": "插座坏了"})
        ok("ordinary routing exposes stable v2 fields",
           s == 200 and first["contract_version"] == 2 and
           [x["id"] for x in first["route_options"]] ==
           ["self_help", "smart_repair"] and
           first["state"] == "route_choice" and first["response_mode"] == "complete")
        ok("Chinese first turn is strictly Chinese",
           first["lang"] == "zh" and has_zh(first["reply"]))
        ok("first turn never asks a follow-up",
           not first["ready"] and not first["missing"] and
           first["next_question"] is None and not first["question_options"])
        s, english_session = stu.call("/api/agent/session", {"lang": "en"})
        english_id = english_session["session"]["id"]
        s, english_first = stu.call("/api/agent/message", {
            "id": english_id, "lang": "en",
            "text": "The dorm chair keeps sinking and cannot be used safely."})
        ok("English first turn has exactly two English routes",
           s == 200 and english_first["lang"] == "en" and
           [x["id"] for x in english_first["route_options"]] ==
           ["self_help", "smart_repair"] and
           not has_zh(english_first["reply"]))
        s, bad_option = stu.call("/api/agent/message", {
            "id": aid, "option": "invented"})
        ok("server rejects invented question option",
           s == 400 and bad_option.get("code") == "bad_question_option" and
           bad_option.get("lang") == "zh" and has_zh(bad_option.get("error")))
        s, other_agent = other.call("/api/agent/session", {"lang": "zh"})
        other_aid = other_agent["session"]["id"]
        s, forbidden_image = other.call("/api/agent/message", {
            "id": other_aid, "text": "请看图片里的故障",
            "image_ids": [agent_image_id]})
        ok("cross-account agent image rejected",
           s == 403 and forbidden_image.get("code") == "forbidden")
        s, second = stu.call("/api/agent/message", {
            "id": aid, "action": "smart_repair",
            "text": "A-301书桌旁插座冒火花，从今天上午开始，已停止使用",
            "image_ids": [agent_image_id]})
        ok("agent produces urgent draft", s == 200 and second["ready"] and
           second["draft"]["priority"] == "urgent" and second["safety_warning"])
        ok("ticket choice enters ticket flow", second["emergency"])
        ok("agent image retained in draft",
           second["draft"]["image_ids"] == [agent_image_id])
        session_view = stu.call("/api/agent/session?id=" + aid)[1]["session"]
        ok("agent message retains image ids",
           session_view["image_ids"] == [agent_image_id] and
           session_view["history"][-2]["image_ids"] == [agent_image_id])
        s, forbidden = other.call("/api/agent/session?id=" + aid)
        ok("cross-account agent session denied", s == 403)
        s, confirmed = stu.call("/api/agent/confirm", {
            "id": aid, "date": day, "slots": [[2, 8]]})
        ok("confirmed draft creates ticket with image", s == 200 and
           confirmed.get("ticket", {}).get("id") and
           confirmed["ticket"]["image_ids"] == [agent_image_id])
        ok("agent confirmation defaults localized surname and registered phone",
           confirmed.get("ticket", {}).get("student_name") == "王同学" and
           confirmed.get("ticket", {}).get("contact") == "13800000001")
        ticket_id = confirmed["ticket"]["id"]
        s, duplicate = stu.call("/api/agent/confirm", {
            "id": aid, "date": day, "slots": [[2, 8]]})
        ok("duplicate confirmation is idempotent",
           s == 200 and duplicate.get("idempotent") and
           duplicate["ticket"]["id"] == ticket_id)

        before_count = len(stu.call("/api/tickets")[1]["tickets"])
        s, advice_session = stu.call("/api/agent/session", {"lang": "zh"})
        advice_id = advice_session["session"]["id"]
        s, advice = stu.call("/api/agent/message", {
            "id": advice_id,
            "text": "宿舍网络突然断线，路由器状态正常，重新插过网线仍无法使用"})
        ok("safe issue first returns exactly two routes",
           s == 200 and advice["state"] == "route_choice" and
           len(advice["route_options"]) == 2 and advice["next_question"] is None)
        s, advice = stu.call("/api/agent/message", {
            "id": advice_id, "action": "self_help"})
        ok("advice starts only after A is selected",
           s == 200 and advice["stage"] == "advice" and advice["self_help"])
        ok("network advice is grounded in the knowledge base",
           advice["kb_status"] == "hit" and
           advice["solution"]["source"] == "knowledge" and
           advice["self_help"] and
           "R-NET" in "".join(advice["solution"].get("chunk_ids") or []) and
           "来源" in advice["self_help"][0])
        s, live_session = stu.call("/api/agent/session", {"lang": "zh"})
        live_id = live_session["session"]["id"]
        s, asked = stu.call("/api/agent/message", {
            "id": live_id, "text": "空调不制冷，滤网看起来有灰，我该怎么修"})
        ok("asking how to repair searches the knowledge base immediately",
           s == 200 and asked["stage"] == "advice" and asked["kb_status"] == "hit" and
           asked["solution"]["source"] == "knowledge")
        s, asked_again = stu.call("/api/agent/message", {
            "id": live_id, "text": "遥控器也没反应，还有什么可以先检查"})
        ok("each repair question searches again",
           s == 200 and asked_again["stage"] == "advice" and
           asked_again["kb_status"] == "hit" and
           asked_again["solution"]["chunk_ids"] != asked["solution"]["chunk_ids"])
        s, ticket_instead = stu.call("/api/agent/message", {
            "id": live_id, "text": "请生成工单"})
        ok("asking for a ticket does not return repair-guide steps",
           s == 200 and ticket_instead["stage"] != "advice" and
           (ticket_instead.get("solution") or {}).get("source") != "knowledge")
        s, advice_follow_1 = stu.call("/api/agent/message", {
            "id": advice_id, "lang": "zh", "text": "路由器的复位按钮在哪里？"})
        s2, advice_follow_2 = stu.call("/api/agent/message", {
            "id": advice_id, "lang": "zh", "text": "还是没恢复，这个步骤安全吗？"})
        ok("advice supports at least two retained follow-up turns",
           s == 200 and s2 == 200 and
           advice_follow_1["state"] == advice_follow_2["state"] == "advice" and
           has_zh(advice_follow_1["reply"]) and has_zh(advice_follow_2["reply"]))
        ok("every advice turn can generate a ticket",
           all(any(route["id"] == "smart_repair" and route["enabled"]
                   for route in item["route_options"])
               for item in (advice, advice_follow_1, advice_follow_2)))
        restored_advice = stu.call(
            "/api/agent/session?id=" + advice_id)[1]["session"]
        ok("refresh restores advice state, history and ticket action",
           restored_advice["lang"] == "zh" and
           restored_advice["state"] == "advice" and
           len(restored_advice["history"]) >= 6 and
           restored_advice["route_options"][0]["id"] == "smart_repair")
        s, no_hit_session = stu.call("/api/agent/session", {"lang": "zh"})
        no_hit_id = no_hit_session["session"]["id"]
        stu.call("/api/agent/message", {
            "id": no_hit_id, "text": "衣柜柜门合页松了，柜门下垂，我没有再去拉"})
        s, no_hit = stu.call("/api/agent/message", {
            "id": no_hit_id, "action": "self_help"})
        ok("KB without a repair procedure routes back to ticket creation",
           s == 200 and no_hit["stage"] == "no_solution" and
           no_hit["kb_status"] == "no_procedure" and no_hit["solution"] is None and
           no_hit["draft"]["repair_type"] == "wardrobe")
        s, zh_switch_session = stu.call("/api/agent/session", {"lang": "zh"})
        zh_switch_id = zh_switch_session["session"]["id"]
        stu.call("/api/agent/message", {
            "id": zh_switch_id, "lang": "zh",
            "text": "宿舍网络断线，路由器亮灯但无法联网"})
        s, switched_en = stu.call("/api/agent/message", {
            "id": zh_switch_id, "lang": "en", "action": "self_help"})
        ok("zh to en changes the next assistant reply",
           s == 200 and switched_en["lang"] == "en" and
           not has_zh(switched_en["reply"]))
        s, en_switch_session = stu.call("/api/agent/session", {"lang": "en"})
        en_switch_id = en_switch_session["session"]["id"]
        stu.call("/api/agent/message", {
            "id": en_switch_id, "lang": "en",
            "text": "The dorm network is offline although the router lights are on."})
        s, switched_zh = stu.call("/api/agent/message", {
            "id": en_switch_id, "lang": "zh", "action": "self_help"})
        switched_session = stu.call(
            "/api/agent/session?id=" + en_switch_id)[1]["session"]
        ok("en to zh changes reply and stored session language",
           s == 200 and switched_zh["lang"] == "zh" and
           has_zh(switched_zh["reply"]) and switched_session["lang"] == "zh" and
           switched_session["history"][-1]["lang"] == "zh")
        s, resolved = stu.call("/api/agent/message", {
            "id": advice_id, "lang": "zh", "action": "resolved"})
        after_count = len(stu.call("/api/tickets")[1]["tickets"])
        ok("resolved advice closes without ticket",
           s == 200 and resolved["stage"] == "resolved" and
           after_count == before_count)

        s, advice_up = stu.call("/api/uploads", {
            "purpose": "agent",
            "images": [{"mime": "image/png", "data": encoded}]})
        advice_image_id = advice_up["uploads"][0]["id"]
        s, midway_session = stu.call("/api/agent/session", {"lang": "zh"})
        midway_id = midway_session["session"]["id"]
        stu.call("/api/agent/message", {
            "id": midway_id, "lang": "zh",
            "text": "宿舍网络断线，路由器亮灯但无法联网，重启后仍未恢复",
            "image_ids": [advice_image_id]})
        stu.call("/api/agent/message", {
            "id": midway_id, "lang": "zh", "action": "self_help",
            "image_ids": [advice_image_id]})
        stu.call("/api/agent/message", {
            "id": midway_id, "lang": "zh", "text": "还是没恢复",
            "image_ids": [advice_image_id]})
        s, midway_draft = stu.call("/api/agent/message", {
            "id": midway_id, "lang": "zh", "action": "smart_repair",
            "image_ids": [advice_image_id]})
        ok("advice-to-ticket preserves draft and image",
           s == 200 and midway_draft["ready"] and
           midway_draft["draft"]["repair_type"] == "network" and
           midway_draft["draft"]["image_ids"] == [advice_image_id] and
           "网络断线" in midway_draft["draft"]["description"])

        s, draft_session = stu.call("/api/agent/session", {"lang": "zh"})
        draft_id = draft_session["session"]["id"]
        stu.call("/api/agent/message", {
            "id": draft_id,
            "text": "宿舍网络突然断线，路由器状态正常，重新插过网线仍无法使用"})
        s, requested = stu.call("/api/agent/message", {
            "id": draft_id, "action": "create_draft"})
        ok("advice can explicitly generate editable draft",
           s == 200 and requested["stage"] == "draft" and
           requested["ready"] and requested["draft"]["repair_type"] == "network")

        s, switch_session = stu.call("/api/agent/session", {"lang": "zh"})
        switch_id = switch_session["session"]["id"]
        stu.call("/api/agent/message", {"id": switch_id, "text": "宿舍椅子一直下沉，今天开始无法正常坐"})
        before_switch = stu.call("/api/agent/session?id=" + switch_id)[1]["session"]["draft"]
        s, manual = stu.call("/api/agent/message", {
            "id": switch_id, "action": "manual"})
        ok("manual switch carries server draft",
           s == 200 and manual["stage"] == "manual" and
           manual["draft"]["description"] == before_switch["description"])
        s, saved = stu.call("/api/agent/message", {
            "id": switch_id, "action": "save_draft",
            "draft": {"repair_type": "chair", "priority": "normal",
                      "title": "椅子下沉（手写）"}})
        restored = stu.call("/api/agent/session?id=" + switch_id)[1]["session"]
        ok("refresh restores latest server draft",
           s == 200 and saved["saved"] and
           restored["draft"]["title"] == "椅子下沉（手写）")
        s, back_to_repair = stu.call("/api/agent/message", {
            "id": switch_id, "action": "smart_repair"})
        ok("route switching preserves edited draft fields",
           s == 200 and back_to_repair["draft"]["title"] == "椅子下沉（手写）")

        s, emergency_session = stu.call("/api/agent/session", {"lang": "zh"})
        emergency_id = emergency_session["session"]["id"]
        s, emergency = stu.call("/api/agent/message", {
            "id": emergency_id, "text": "插座正在冒火花并有焦味"})
        ok("emergency first turn keeps both routes and no question",
           s == 200 and emergency["emergency"] and
           [x["id"] for x in emergency["route_options"]] ==
           ["self_help", "smart_repair"] and emergency["next_question"] is None and
           emergency["route_options"][1]["recommended"])
        s, safe_advice = stu.call("/api/agent/message", {
            "id": emergency_id, "action": "self_help"})
        ok("emergency A returns conservative safety advice",
           s == 200 and safe_advice["stage"] == "advice" and
           safe_advice["solution"]["source"] == "safety_fallback" and
           "拆卸" in safe_advice["self_help"][0])
        dangerous_text = " ".join(safe_advice["self_help"])
        ok("dangerous advice contains safety actions only",
           "远离" in dangerous_text and "联系" in dangerous_text and
           "打开外壳" not in dangerous_text and "拆开维修" not in dangerous_text)

        s, human_session = stu.call("/api/agent/session", {"lang": "en"})
        human_id = human_session["session"]["id"]
        s, human = stu.call("/api/agent/message", {
            "id": human_id, "action": "human"})
        ok("removed human action is rejected",
           s == 400 and human.get("code") == "bad_agent_action" and
           human.get("lang") == "en" and not has_zh(human.get("error")))
        from app import Handler
        legacy = Handler._agent_session_view({
            "id": "legacy", "stage": "human_handoff", "state": "human_handoff",
            "route_options": [{"id": "human", "enabled": True}],
        })
        ok("legacy human session normalizes to route choice",
           legacy["state"] == "route_choice" and
           [x["id"] for x in legacy["route_options"]] ==
           ["self_help", "smart_repair"])

        s, fallback_session = stu.call("/api/agent/session", {"lang": "zh"})
        fallback_id = fallback_session["session"]["id"]
        s, fallback = stu.call("/api/agent/message", {
            "id": fallback_id, "text": "宿舍门锁卡住，钥匙可以转动但门打不开，今晚开始"})
        ok("no-model deployment uses deterministic fallback",
           s == 200 and fallback["source"] == "rules")
        print("ALL MULTIMODAL TESTS PASSED")
        return 0
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=8)
        except Exception:
            proc.kill()
        if "Traceback" in server_output(log_path):
            print(server_output(log_path))


if __name__ == "__main__":
    sys.exit(main())
