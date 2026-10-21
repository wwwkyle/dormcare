"""Focused upload, completion-proof and intake-agent tests."""
import base64
import os
import sys
import time
from pathlib import Path

from testclient import (DEMO_STUDENT, DEMO_STUDENT_2, DEMO_WORKERS, Client,
                        reset_store, server_output, spawn_server, store_path)

HERE = Path(__file__).resolve().parent
PORT = 3118
BASE = "http://127.0.0.1:%d" % PORT
PNG = b"\x89PNG\r\n\x1a\n" + b"\0" * 24


def ok(label, condition):
    print(("PASS  " if condition else "FAIL  ") + label)
    if not condition:
        raise AssertionError(label)


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

        s, agent_up = stu.call("/api/uploads", {
            "purpose": "agent", "images": [{"mime": "image/png", "data": encoded}]})
        agent_image_id = agent_up["uploads"][0]["id"]
        ok("agent image upload succeeds", s == 200 and agent_image_id)

        s, agent = stu.call("/api/agent/session", {"lang": "zh"})
        aid = agent["session"]["id"]
        s, first = stu.call("/api/agent/message", {"id": aid, "text": "插座坏了"})
        ok("ordinary routing exposes stable v2 fields",
           s == 200 and first["contract_version"] == 2 and
           len(first["route_options"]) == 2 and first["response_mode"] == "complete")
        ok("agent asks exactly one question with options",
           not first["ready"] and len(first["missing"]) == 1 and
           first["next_question"] and first["question_options"])
        s, bad_option = stu.call("/api/agent/message", {
            "id": aid, "option": "invented"})
        ok("server rejects invented question option",
           s == 400 and bad_option.get("code") == "bad_question_option")
        s, other_agent = other.call("/api/agent/session", {"lang": "zh"})
        other_aid = other_agent["session"]["id"]
        s, forbidden_image = other.call("/api/agent/message", {
            "id": other_aid, "text": "请看图片里的故障",
            "image_ids": [agent_image_id]})
        ok("cross-account agent image rejected",
           s == 403 and forbidden_image.get("code") == "forbidden")
        s, second = stu.call("/api/agent/message", {
            "id": aid, "text": "A-301书桌旁插座冒火花，从今天上午开始，已停止使用",
            "image_ids": [agent_image_id]})
        ok("agent produces urgent draft", s == 200 and second["ready"] and
           second["draft"]["priority"] == "urgent" and second["safety_warning"])
        ok("emergency forces smart repair route",
           second["emergency"] and
           [x["id"] for x in second["route_options"]] == ["smart_repair"])
        s, blocked_self_help = stu.call("/api/agent/message", {
            "id": aid, "action": "self_help"})
        ok("emergency self-help is blocked",
           s == 409 and blocked_self_help.get("code") == "emergency_requires_report")
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
        ok("safe low-risk issue returns advice branch",
           s == 200 and advice["stage"] == "advice" and
           advice["self_help"] and not advice["ready"])
        ok("empty KB never masquerades as a hit",
           advice["kb_status"] == "empty" and
           advice["solution"]["source"] == "fallback" and
           advice["solution"]["safety_reviewed"])
        s, no_hit_session = stu.call("/api/agent/session", {"lang": "zh"})
        no_hit_id = no_hit_session["session"]["id"]
        stu.call("/api/agent/message", {
            "id": no_hit_id, "text": "宿舍插座完全没有电，其他插座正常，外观没有异常"})
        s, no_hit = stu.call("/api/agent/message", {
            "id": no_hit_id, "action": "self_help"})
        ok("KB no-hit explicitly routes back to ticket creation",
           s == 200 and no_hit["stage"] == "no_solution" and
           no_hit["kb_status"] == "empty" and no_hit["solution"] is None and
           no_hit["draft"]["repair_type"] == "power")
        s, resolved = stu.call("/api/agent/message", {
            "id": advice_id, "action": "resolved"})
        after_count = len(stu.call("/api/tickets")[1]["tickets"])
        ok("resolved advice closes without ticket",
           s == 200 and resolved["stage"] == "resolved" and
           after_count == before_count)

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

        s, human_session = stu.call("/api/agent/session", {"lang": "en"})
        human_id = human_session["session"]["id"]
        s, human = stu.call("/api/agent/message", {
            "id": human_id, "action": "human"})
        ok("human handoff is explicit placeholder state",
           s == 200 and human["agent_status"] == "human_requested" and
           human["stage"] == "human_handoff" and "not connected" in human["reply"])

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
