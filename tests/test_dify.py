"""Dify transport, intake contract and authenticated HTTP integration tests."""
import copy
import base64
import http.client
import io
import json
import os
import socket
import sys
import tempfile
import threading
import unittest
import urllib.error
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from email.parser import BytesParser
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import dify_client
import intake_agent
from config import REPAIR_TYPES
from scheduler import PRIORITY_LEVELS
from testclient import DEMO_STUDENT, DEMO_STUDENT_2, Client, spawn_server


USER = {"id": "U-test", "role": "student", "college": "shaw",
        "block": "东座 1 栋", "dorm": "A-301"}
SESSION = {"id": "A-test", "history": [{
    "role": "user", "content": "宿舍空调开机后一直不制冷，已经持续两天。"}],
    "image_ids": ["IMG-owned"]}
RESPONSE = {"answer": "请先检查遥控器的制冷模式，不要拆机。",
            "conversation_id": "conversation-1", "event": "message"}
PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aDegAAAAASUVORK5CYII=")


class DifyClientTests(unittest.TestCase):
    def setUp(self):
        self.environment = patch.dict(os.environ, {"DIFY_API_KEY": "app-test-secret"}, clear=True)
        self.environment.start()
        self.addCleanup(self.environment.stop)

    def invoke(self, result=None, session=None, lang="zh", **options):
        response = io.BytesIO(json.dumps(RESPONSE if result is None else result).encode("utf-8"))
        with patch("dify_client.urllib.request.build_opener") as factory:
            factory.return_value.open.return_value = response
            result = dify_client.chat(dict(session or SESSION, image_ids=[]), USER, lang, **options)
            request = factory.return_value.open.call_args[0][0]
            timeout = factory.return_value.open.call_args[1]["timeout"]
        return result, request, timeout

    def test_authenticated_blocking_request(self):
        result, request, timeout = self.invoke()
        body = json.loads(request.data)
        self.assertEqual(request.full_url, "https://api.dify.ai/v1/chat-messages")
        self.assertEqual(request.get_header("Authorization"), "Bearer app-test-secret")
        self.assertEqual(body["response_mode"], "blocking")
        self.assertEqual(body["conversation_id"], "")
        self.assertEqual(body["user"], "dormcare-U-test")
        self.assertEqual(body["inputs"]["complaint"], SESSION["history"][0]["content"])
        self.assertEqual(json.loads(body["inputs"]["location_json"])["dorm"], "A-301")
        self.assertNotIn("files", body)
        self.assertNotIn("附件尚未发送", body["query"])
        self.assertEqual(timeout, 120)
        self.assertEqual(result, RESPONSE)

    def test_continue_conversation_and_ticket_action(self):
        session = dict(SESSION, dify_conversation_id="prior-conversation", dify_lang="zh")
        _result, request, _timeout = self.invoke(session=session, force_draft=True)
        body = json.loads(request.data)
        self.assertEqual(body["conversation_id"], "prior-conversation")
        self.assertEqual(body["inputs"]["route"], "direct_report")
        self.assertEqual(body["inputs"]["resolved_after_check"], "no")
        self.assertIn("尚未提交工单", body["query"])

    def test_continuing_query_sends_only_the_current_turn(self):
        session = dict(SESSION, dify_conversation_id="prior-conversation", dify_lang="zh",
                       history=SESSION["history"] + [
                           {"role": "assistant", "content": "检查后是否恢复？"},
                           {"role": "user", "content": "还是不行，需要师傅上门。"}])
        _result, request, _timeout = self.invoke(session=session)
        query = json.loads(request.data)["query"]
        self.assertIn("还是不行，需要师傅上门。", query)
        self.assertNotIn(SESSION["history"][0]["content"], query)
        self.assertNotIn("请提供安全的排查建议", query)

    def test_ticket_intent_is_explicit_in_query(self):
        _result, request, _timeout = self.invoke(force_draft=True)
        query = json.loads(request.data)["query"]
        self.assertIn("仍需师傅", query)
        self.assertIn("未解决", query)

    def test_new_conversation_keeps_prior_user_context(self):
        session = dict(SESSION, dify_conversation_id="prior-conversation", dify_lang="zh",
                       history=SESSION["history"] + [
                           {"role": "user", "content": "It is still broken."}])
        _result, request, _timeout = self.invoke(session=session, lang="en")
        body = json.loads(request.data)
        self.assertEqual(body["conversation_id"], "")
        self.assertIn(SESSION["history"][0]["content"], body["query"])
        self.assertIn("It is still broken.", body["query"])

    def test_language_switch_starts_new_conversation(self):
        session = dict(SESSION, dify_conversation_id="prior-conversation", dify_lang="zh")
        _result, request, _timeout = self.invoke(session=session, lang="en")
        self.assertEqual(json.loads(request.data)["conversation_id"], "")

    def test_custom_inputs_do_not_override_trusted_context(self):
        os.environ["DIFY_INPUTS_JSON"] = json.dumps({"custom_field": "value", "lang": "en"})
        session = dict(SESSION, history=SESSION["history"] + [{
            "role": "user", "content": dify_client.action_text("self_help"),
            "action": "self_help",
        }])
        _result, request, _timeout = self.invoke(session=session, advice_mode=True)
        inputs = json.loads(request.data)["inputs"]
        self.assertEqual(inputs["custom_field"], "value")
        self.assertEqual(inputs["lang"], "zh")
        self.assertEqual(inputs["route"], "self_help")

    def test_invalid_configuration_fails_before_network(self):
        for name, value in (("DIFY_API_BASE_URL", "http://example.com/v1"),
                            ("DIFY_API_BASE_URL", "https://user:secret@example.com/v1"),
                            ("DIFY_INPUTS_JSON", "[]"), ("DIFY_INPUTS_JSON", "invalid"),
                            ("DIFY_TIMEOUT_SECONDS", "0"), ("DIFY_TIMEOUT_SECONDS", "nan")):
            with self.subTest(name=name, value=value), patch.dict(os.environ, {name: value}):
                with patch("dify_client.urllib.request.build_opener") as factory:
                    with self.assertRaises(dify_client.DifyError):
                        dify_client.chat(SESSION, USER)
                    factory.assert_not_called()

    def test_malformed_responses_are_rejected(self):
        for result in ([], {}, dict(RESPONSE, answer=""), dict(RESPONSE, answer={}),
                       dict(RESPONSE, conversation_id=None), dict(RESPONSE, event="error")):
            with self.subTest(result=result), self.assertRaises(dify_client.DifyError):
                self.invoke(result=result)

    def test_transport_errors_do_not_leak_secrets(self):
        errors = [TimeoutError("app-test-secret"),
                  http.client.IncompleteRead(b"app-test-secret"),
                  urllib.error.HTTPError("https://example.com", 401, "app-test-secret", {}, None)]
        for error in errors:
            with self.subTest(error=error), patch("dify_client.urllib.request.build_opener") as factory:
                factory.return_value.open.side_effect = error
                with self.assertRaises(dify_client.DifyError) as caught:
                    dify_client.chat(dict(SESSION, image_ids=[]), USER)
                self.assertNotIn("app-test-secret", str(caught.exception))


class DifyImageTests(unittest.TestCase):
    def setUp(self):
        self.environment = patch.dict(os.environ, {"DIFY_API_KEY": "app-image-secret"}, clear=True)
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self.directory = tempfile.TemporaryDirectory(prefix="dormcare-image-")
        self.addCleanup(self.directory.cleanup)
        Path(self.directory.name, "fixture.png").write_bytes(PNG)
        self.store = Mock()
        self.store.upload.return_value = {
            "id": "IMG-owned", "owner_user_id": USER["id"], "purpose": "agent",
            "ticket_id": "", "filename": "fixture.png", "mime": "image/png", "size": len(PNG),
        }
        self.requests = []

    def invoke(self, session=None, upload_result=None):
        def open_request(request, **_options):
            self.requests.append(request)
            response = (upload_result or {"id": "remote-image-1"}) if request.full_url.endswith("/files/upload") else RESPONSE
            return io.BytesIO(json.dumps(response).encode())

        with patch("dify_client.urllib.request.build_opener") as factory:
            factory.return_value.open.side_effect = open_request
            return dify_client.chat(session or copy.deepcopy(SESSION), USER,
                                    store=self.store, upload_dir=self.directory.name)

    def test_image_bytes_are_uploaded_and_referenced_in_chat(self):
        result = self.invoke()
        self.assertEqual(len(self.requests), 2)
        upload, chat = self.requests
        self.assertEqual(upload.full_url, "https://api.dify.ai/v1/files/upload")
        self.assertIn("multipart/form-data; boundary=", upload.get_header("Content-type"))
        self.assertEqual(upload.get_header("Authorization"), "Bearer app-image-secret")
        self.assertIn(PNG, upload.data)
        self.assertIn(b'name="user"', upload.data)
        self.assertIn(b"dormcare-U-test", upload.data)
        self.assertIn(b"Content-Type: image/png", upload.data)
        body = json.loads(chat.data)
        self.assertEqual(body["files"], [{"type": "image", "transfer_method": "local_file",
                                         "upload_file_id": "remote-image-1"}])
        self.assertNotIn("附件尚未发送", body["query"])
        self.assertEqual(result["dify_files"]["IMG-owned"], "remote-image-1")

    def test_follow_up_reuses_remote_file_for_same_application_and_user(self):
        first = self.invoke()
        self.requests.clear()
        session = dict(SESSION, dify_files=first["dify_files"],
                       dify_files_scope=first["dify_files_scope"],
                       dify_conversation_id="conversation-1", dify_lang="zh")
        self.invoke(session)
        self.assertEqual(len(self.requests), 1)
        self.assertTrue(self.requests[0].full_url.endswith("/chat-messages"))
        self.assertEqual(json.loads(self.requests[0].data)["files"][0]["upload_file_id"], "remote-image-1")

    def test_changed_api_key_uploads_again(self):
        first = self.invoke()
        self.requests.clear()
        session = dict(SESSION, dify_files=first["dify_files"], dify_files_scope=first["dify_files_scope"])
        os.environ["DIFY_API_KEY"] = "app-another-key"
        self.invoke(session)
        self.assertEqual(len(self.requests), 2)

    def test_removed_image_is_not_forwarded_again(self):
        first = self.invoke()
        self.requests.clear()
        self.invoke(dict(SESSION, image_ids=[], dify_files=first["dify_files"],
                         dify_files_scope=first["dify_files_scope"]))
        self.assertEqual(len(self.requests), 1)
        self.assertFalse(json.loads(self.requests[0].data).get("files"))

    def test_all_three_allowed_images_are_forwarded(self):
        record = self.store.upload.return_value
        self.store.upload.side_effect = lambda identifier: dict(record, id=identifier)
        result = self.invoke(dict(SESSION, image_ids=["first", "second", "third"]))
        self.assertEqual(len(self.requests), 4)
        self.assertEqual(len(json.loads(self.requests[-1].data)["files"]), 3)
        self.assertEqual(len(result["dify_files"]), 3)

    def test_invalid_ownership_or_local_file_never_reaches_dify(self):
        for patch_values in ({"owner_user_id": "another-user"}, {"purpose": "completion"},
                             {"ticket_id": "T-used"}, {"filename": "missing.png"}):
            with self.subTest(patch_values=patch_values):
                record = dict(self.store.upload.return_value, **patch_values)
                with patch.object(self.store, "upload", return_value=record):
                    with self.assertRaises(dify_client.DifyError):
                        self.invoke()
                self.assertEqual(self.requests, [])

    def test_workflow_image_limit_is_enforced_without_truncating(self):
        with self.assertRaises(dify_client.DifyError):
            self.invoke(dict(SESSION, image_ids=["one", "two", "three", "four"]))
        self.assertEqual(self.requests, [])

    def test_mismatched_image_bytes_are_rejected_before_upload(self):
        Path(self.directory.name, "fixture.png").write_bytes(b"not an image")
        with self.assertRaises(dify_client.DifyError):
            self.invoke()
        self.assertEqual(self.requests, [])

    def test_missing_upload_id_stops_before_chat(self):
        with self.assertRaises(dify_client.DifyError):
            self.invoke(upload_result={"unexpected": "response"})
        self.assertEqual(len(self.requests), 1)


class DifyIntakeTests(unittest.TestCase):
    def setUp(self):
        self.environment = patch.dict(os.environ, {"DIFY_API_KEY": "app-test-secret"}, clear=True)
        self.environment.start()
        self.addCleanup(self.environment.stop)

    def respond(self, answer=None, session=None, **options):
        response = dict(RESPONSE, answer=answer or RESPONSE["answer"])
        with patch("dify_client.chat", return_value=response):
            return intake_agent.respond("", "", session or copy.deepcopy(SESSION), USER, **options)

    def test_plain_answer_preserves_v2_contract_and_attachments(self):
        result = self.respond(advice_mode=True)
        self.assertEqual(result["source"], "dify")
        self.assertEqual(result["contract_version"], 2)
        self.assertEqual(result["stage"], "advice")
        self.assertFalse(result["complete"])
        self.assertEqual(result["draft"]["image_ids"], ["IMG-owned"])
        self.assertEqual(result["dify_conversation_id"], "conversation-1")
        self.assertEqual(result["self_help"], [])

    def test_workflow_json_maps_to_ticket_without_trusting_identity(self):
        answer = "已整理：\n```json\n" + json.dumps({
            "ok": True, "message": "报修草稿已整理，请核对。", "route": "direct_report",
            "ticket_payload": {"title": "空调制冷故障", "repair_type": "ac_service",
                               "priority": "high", "description": "空调持续不制冷。",
                               "image_ids": ["IMG-invented"], "user_id": "someone-else",
                               "college": "muse", "dorm": "Z-999"},
        }, ensure_ascii=False) + "\n```"
        result = self.respond(answer)
        self.assertTrue(result["complete"])
        self.assertEqual(result["draft"]["repair_type"], "ac_service")
        self.assertEqual(result["draft"]["priority"], "high")
        self.assertEqual(result["draft"]["image_ids"], ["IMG-owned"])
        self.assertNotIn("user_id", result["draft"])
        self.assertNotIn("college", result["draft"])
        self.assertEqual(result["draft"]["location"], "东座 1 栋 A-301")

    def test_latest_workflow_flat_draft_uses_ai_fields(self):
        result = self.respond(json.dumps({
            "status": "draft", "title": "椅子靠背松动", "category": "椅子", "urgency": "较急",
            "description": "学生确认椅子靠背摇晃，已停止使用。", "is_public_area": False,
            "college": "思廷书院", "building": "西座2栋", "location": "Z-999",
        }, ensure_ascii=False))
        self.assertEqual(result["source"], "dify")
        self.assertEqual(result["stage"], "draft")
        self.assertTrue(result["complete"])
        self.assertEqual(result["draft"]["title"], "椅子靠背松动")
        self.assertEqual(result["draft"]["repair_type"], "chair")
        self.assertEqual(result["draft"]["priority"], "high")
        self.assertEqual(result["draft"]["description"], "学生确认椅子靠背摇晃，已停止使用。")
        self.assertFalse(result["draft"]["public_area"])
        self.assertEqual(result["draft"]["location"], "东座 1 栋 A-301")
        self.assertNotIn("college", result["draft"])
        self.assertNotIn("block", result["draft"])
        self.assertNotIn("dorm", result["draft"])
        self.assertNotIn('"status"', result["reply"])

    def test_latest_workflow_maps_all_catalogue_labels_without_keyword_detection(self):
        for repair in REPAIR_TYPES:
            with self.subTest(category=repair["zh"]):
                result = self.respond(json.dumps({
                    "status": "draft", "title": "设施故障", "category": repair["zh"],
                    "urgency": "一般", "description": "学生描述的故障。", "is_public_area": False,
                }, ensure_ascii=False))
                self.assertEqual(result["draft"]["repair_type"], repair["id"])
        for priority, labels in PRIORITY_LEVELS.items():
            with self.subTest(urgency=labels["zh"]):
                result = self.respond(json.dumps({
                    "status": "draft", "category": "椅子", "urgency": labels["zh"],
                    "description": "设施损坏。",
                }, ensure_ascii=False))
                self.assertEqual(result["draft"]["priority"], priority)

    def test_latest_workflow_missing_description_does_not_create_local_draft(self):
        result = self.respond(json.dumps({
            "status": "draft", "category": "灯具 / 照明", "urgency": "一般", "description": "",
        }, ensure_ascii=False))
        self.assertFalse(result["complete"])
        self.assertEqual(result["source"], "dify")
        self.assertEqual(result["stage"], "follow_up")

    def test_latest_workflow_unknown_category_does_not_use_local_keyword_classification(self):
        result = self.respond(json.dumps({
            "status": "draft", "category": "无法识别", "urgency": "未知", "description": "设施异常。",
        }, ensure_ascii=False))
        self.assertEqual(result["draft"]["repair_type"], "other")
        self.assertEqual(result["draft"]["priority"], "normal")

    def test_latest_workflow_public_location_defaults_to_account_college_and_block(self):
        for address in ({"college": "思廷书院", "building": "西座2栋"},
                        {"college": "", "building": ""}, {}):
            with self.subTest(address=address):
                result = self.respond(json.dumps({
                    "status": "draft", "category": "灯具 / 照明", "urgency": "一般",
                    "description": "三楼走廊灯不亮。", "is_public_area": True,
                    "location": "三楼东侧走廊", **address,
                }, ensure_ascii=False))
                self.assertTrue(result["draft"]["public_area"])
                self.assertEqual(result["draft"]["college"], USER["college"])
                self.assertEqual(result["draft"]["block"], USER["block"])
                self.assertEqual(result["draft"]["dorm"], "三楼东侧走廊")

    def test_latest_workflow_missing_public_location_does_not_use_account_dorm(self):
        result = self.respond(json.dumps({
            "status": "draft", "category": "灯具 / 照明", "urgency": "一般",
            "description": "三楼走廊灯不亮。", "is_public_area": True,
        }, ensure_ascii=False))
        self.assertEqual(result["draft"]["college"], USER["college"])
        self.assertEqual(result["draft"]["block"], USER["block"])
        self.assertEqual(result["draft"]["dorm"], "")

    def test_latest_workflow_plain_resolution_can_close(self):
        session = dict(SESSION, history=SESSION["history"] + [{
            "role": "user", "content": dify_client.action_text("resolved"), "action": "resolved",
        }])
        result = self.respond("好的，问题解决了就好！如果之后还有其他设施问题，随时来找我。", session=session)
        self.assertEqual(result["stage"], "resolved")
        self.assertIsNone(result["draft"])

    def test_latest_workflow_fixed_follow_up_is_localized_and_not_a_draft(self):
        result = self.respond(intake_agent.DIFY_COLLECTING_REPLY, lang="en")
        self.assertEqual(result["source"], "dify")
        self.assertEqual(result["stage"], "follow_up")
        self.assertFalse(result["complete"])
        self.assertEqual(result["question_options"], [])
        self.assertIn("facility", result["reply"])

    def test_image_failure_is_explicit_and_cannot_become_keyword_draft(self):
        with patch("dify_client.chat", side_effect=dify_client.DifyError("http_413")):
            result = intake_agent.respond("", "", copy.deepcopy(SESSION), USER, force_draft=True)
        self.assertFalse(result["complete"])
        self.assertEqual(result["source"], "dify_error")
        self.assertIn("图片", result["reply"])
        self.assertEqual(result["question_options"], [])

    def test_structured_steps_preserve_source_without_claiming_safety_review(self):
        result = self.respond(json.dumps({
            "message": "可以检查遥控器设置。", "route": "self_help",
            "knowledge": {"steps": [{"text": "检查遥控器模式。"}, "不要拆机。"]},
        }, ensure_ascii=False))
        self.assertEqual(result["self_help"], ["检查遥控器模式。", "不要拆机。"])
        self.assertEqual(result["solution"]["source"], "dify")
        self.assertFalse(result["solution"]["safety_reviewed"])

    def test_ticket_request_does_not_turn_plain_text_into_local_draft(self):
        result = self.respond(force_draft=True)
        self.assertFalse(result["complete"])
        self.assertEqual(result["stage"], "advice")
        self.assertEqual(result["reply"], RESPONSE["answer"])

    def test_remote_draft_is_not_blocked_by_short_initial_description(self):
        session = dict(SESSION, history=[{"role": "user", "content": "灯不亮"}])
        result = self.respond(json.dumps({
            "reply": "报修草稿已整理，请确认。", "stage": "draft", "complete": True,
            "draft": {"title": "照明故障", "repair_type": "light", "priority": "normal",
                      "description": "宿舍灯具无法点亮，需要师傅检修。"},
        }, ensure_ascii=False), session=session, force_draft=True)
        self.assertEqual(result["source"], "dify")
        self.assertEqual(result["stage"], "draft")
        self.assertTrue(result["complete"])
        self.assertEqual(result["draft"]["repair_type"], "light")
        self.assertEqual(result["question_options"], [])

    def test_ticket_request_preserves_remote_follow_up_and_question(self):
        session = dict(SESSION, history=[{"role": "user", "content": "灯不亮"}])
        question = "开关打开后，是完全不亮还是闪烁？"
        result = self.respond(json.dumps({
            "reply": question, "route": "direct_report", "stage": "follow_up",
            "complete": False, "missing": [question],
        }, ensure_ascii=False), session=session, force_draft=True)
        self.assertFalse(result["complete"])
        self.assertEqual(result["stage"], "follow_up")
        self.assertEqual(result["reply"], question)
        self.assertEqual(result["missing"], [question])
        self.assertEqual(result["next_question"]["text"], question)
        self.assertEqual(result["question_options"], [])

    def test_ticket_route_can_still_be_incomplete(self):
        result = self.respond(json.dumps({
            "reply": "需要再确认故障现象。", "route": "direct_report", "complete": False,
        }, ensure_ascii=False), force_draft=True)
        self.assertFalse(result["complete"])
        self.assertEqual(result["stage"], "follow_up")

    def test_workflow_can_close_a_resolved_conversation(self):
        result = self.respond(json.dumps({
            "reply": "问题已解决，本次不生成工单。", "stage": "resolved",
        }, ensure_ascii=False))
        self.assertEqual(result["stage"], "resolved")
        self.assertFalse(result["complete"])
        self.assertIsNone(result["draft"])

    def test_remote_follow_up_preserves_all_requested_details(self):
        questions = ["故障从什么时候开始？", "现在是否完全无法使用？"]
        result = self.respond(json.dumps({
            "reply": "请补充两项信息。", "stage": "follow_up", "missing": questions,
        }, ensure_ascii=False))
        self.assertEqual(result["missing"], questions)

    def test_button_intent_does_not_inflate_local_fault_description(self):
        session = dict(SESSION, history=[
            {"role": "user", "content": "灯不亮"},
            {"role": "user", "content": dify_client.action_text("create_draft"),
             "action": "create_draft"},
        ])
        with patch.dict(os.environ, {"DIFY_API_KEY": ""}):
            result = intake_agent.respond("", "", session, USER, force_draft=True)
        self.assertFalse(result["complete"])
        self.assertEqual(result["draft"]["description"], "灯不亮")

    def test_action_with_fault_details_retains_emergency_guard(self):
        session = dict(SESSION, history=[{
            "role": "user", "content": dify_client.action_text("create_draft") + "\n插座冒烟",
            "fault_text": "插座冒烟", "action": "create_draft",
        }])
        with patch("dify_client.chat") as remote:
            result = intake_agent.respond("", "", session, USER, force_draft=True)
        remote.assert_not_called()
        self.assertTrue(result["emergency"])
        self.assertEqual(result["draft"]["description"], "插座冒烟")

    def test_workflow_follow_up_remains_incomplete(self):
        result = self.respond(json.dumps({
            "reply": "空调是否能正常启动？", "stage": "follow_up", "complete": False,
            "missing": ["能否正常启动？"],
        }, ensure_ascii=False))
        self.assertEqual(result["stage"], "follow_up")
        self.assertFalse(result["complete"])
        self.assertEqual(result["missing"], ["能否正常启动？"])

    def test_unknown_catalogue_values_are_ignored(self):
        result = self.respond(json.dumps({
            "reply": "信息已经整理。", "route": "direct_report",
            "draft": {"repair_type": ["invented"], "priority": "invented"},
        }, ensure_ascii=False))
        self.assertEqual(result["draft"]["repair_type"], "ac")
        self.assertEqual(result["draft"]["priority"], "normal")

    def test_local_emergency_bypasses_remote_workflow(self):
        session = dict(SESSION, history=[{"role": "user", "content": "插座冒烟了"}])
        with patch("dify_client.chat") as remote:
            result = intake_agent.respond("", "", session, USER, advice_mode=True)
        remote.assert_not_called()
        self.assertTrue(result["emergency"])
        self.assertTrue(result["complete"])
        self.assertEqual(result["draft"]["priority"], "urgent")
        self.assertIn("远离危险区域", result["reply"])

    def test_remote_emergency_cannot_offer_self_help(self):
        result = self.respond(json.dumps({
            "message": "请自行拆开检查。", "route": "self_help", "emergency": True,
        }, ensure_ascii=False))
        self.assertTrue(result["emergency"])
        self.assertEqual(result["draft"]["priority"], "urgent")
        self.assertEqual(result["stage"], "draft")
        self.assertNotIn("请自行拆开", result["reply"])

    def test_remote_emergency_does_not_wait_for_more_information(self):
        session = dict(SESSION, history=[{"role": "user", "content": "设备异常"}])
        result = self.respond(json.dumps({"reply": "发现紧急风险。", "emergency": True},
                                         ensure_ascii=False), session=session)
        self.assertTrue(result["complete"])
        self.assertEqual(result["stage"], "draft")
        self.assertEqual(result["draft"]["priority"], "urgent")

    def test_outage_falls_back_to_local_knowledge(self):
        with patch("dify_client.chat", side_effect=dify_client.DifyError("http_401")):
            result = intake_agent.respond("", "", dict(SESSION, image_ids=[]), USER, advice_mode=True)
        self.assertEqual(result["source"], "fallback_after_dify_error")
        self.assertIn(result["stage"], ("advice", "no_solution"))
        self.assertNotIn("保存模型密钥", result["reply"])

    def test_wrong_language_image_response_is_explicit_without_losing_attachments(self):
        result = self.respond("This is an English reply.", force_draft=True)
        self.assertEqual(result["source"], "dify_error")
        self.assertFalse(result["complete"])
        self.assertEqual(result["draft"]["image_ids"], ["IMG-owned"])

    def test_disabled_integration_keeps_original_flow(self):
        with patch.dict(os.environ, {"DIFY_API_KEY": ""}), patch("dify_client.chat") as remote:
            result = intake_agent.respond("", "", SESSION, USER, force_draft=True)
        remote.assert_not_called()
        self.assertEqual(result["source"], "rules")
        self.assertTrue(result["complete"])


class FakeDifyHandler(BaseHTTPRequestHandler):
    requests = []
    answers = []
    fail_uploads = False

    def log_message(self, *_arguments):
        pass

    def do_POST(self):
        raw = self.rfile.read(int(self.headers["Content-Length"]))
        uploading = self.path.endswith("/files/upload")
        if uploading:
            multipart = BytesParser().parsebytes(
                ("Content-Type: " + self.headers["Content-Type"] + "\r\nMIME-Version: 1.0\r\n\r\n").encode() + raw)
            body = {part.get_param("name", header="content-disposition"): part.get_payload(decode=True)
                    for part in multipart.get_payload()}
            response = {"id": "http-upload-1"}
        else:
            body = json.loads(raw)
            response = dict(RESPONSE, conversation_id=body.get("conversation_id") or "http-conversation")
            if self.answers:
                response["answer"] = self.answers.pop(0)
        self.requests.append((self.path, self.headers.get("Authorization"), body))
        encoded = json.dumps(response).encode("utf-8")
        self.send_response(413 if uploading and self.fail_uploads else (201 if uploading else 200))
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)


class DifyHTTPTests(unittest.TestCase):
    def test_student_chat_persistence_and_confirmation(self):
        here = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory(prefix="dormcare-dify-") as temporary:
            provider = ThreadingHTTPServer(("127.0.0.1", 0), FakeDifyHandler)
            thread = threading.Thread(target=provider.serve_forever, daemon=True)
            thread.start()
            with socket.socket() as listener:
                listener.bind(("127.0.0.1", 0))
                port = listener.getsockname()[1]
            process = None
            FakeDifyHandler.requests = []
            FakeDifyHandler.fail_uploads = False
            FakeDifyHandler.answers = [
                RESPONSE["answer"],
                json.dumps({"reply": "灯具是完全不亮还是闪烁？", "stage": "follow_up",
                            "route": "direct_report", "complete": False,
                            "missing": ["灯具是完全不亮还是闪烁？"]}, ensure_ascii=False),
                json.dumps({"status": "draft", "title": "照明故障", "category": "灯具 / 照明",
                            "description": "灯具完全不亮，需要安排师傅检修。", "urgency": "一般",
                            "is_public_area": False, "college": "", "building": "", "location": ""},
                           ensure_ascii=False),
                RESPONSE["answer"],
                "好的，问题解决了就好！如果之后还有其他设施问题，随时来找我。",
            ]
            try:
                process, _log = spawn_server(here, port, "", {
                    "DIFY_API_KEY": "app-http-secret", "OPENAI_API_KEY": "",
                    "DIFY_API_BASE_URL": "http://127.0.0.1:%d/v1" % provider.server_port,
                    "DIFY_INPUTS_JSON": "{}", "DORMCARE_DIFY_API_KEY": "callback-secret",
                }, log_name=os.path.join(temporary, "server.log"),
                    data=os.path.join(temporary, "store.json"))
                base = "http://127.0.0.1:%d" % port
                anonymous = Client(base)
                self.assertTrue(anonymous.call("/api/health")[1]["dify_enabled"])
                self.assertEqual(anonymous.call("/api/agent/session", {})[0], 401)
                complaint = {"complaint": "房间插座冒烟"}
                self.assertEqual(anonymous.call("/api/dify/workflow", complaint)[0], 401)
                status, callback = anonymous.call("/api/dify/workflow", complaint,
                    headers={"X-DormCare-Key": "callback-secret"})
                self.assertEqual(status, 200)
                self.assertTrue(callback["emergency"])
                self.assertFalse(callback["created_ticket"])
                self.assertEqual(FakeDifyHandler.requests, [])
                student = Client(base, *DEMO_STUDENT)
                other = Client(base, *DEMO_STUDENT_2)
                self.assertEqual(student.call("/api/config")[1]["agent_image_limit"], 3)
                status, uploaded = student.call("/api/uploads", {
                    "purpose": "agent", "images": [{"mime": "image/png", "data": base64.b64encode(PNG).decode()}]})
                self.assertEqual(status, 200)
                image_id = uploaded["uploads"][0]["id"]
                session_id = student.call("/api/agent/session", {"lang": "zh"})[1]["session"]["id"]
                status, first = student.call("/api/agent/message", {
                    "id": session_id, "lang": "zh", "text": "宿舍灯不亮", "image_ids": [image_id]})
                self.assertEqual(status, 200)
                self.assertEqual(first["source"], "dify")
                self.assertEqual(first["image_delivery"], {"status": "sent", "count": 1})
                self.assertEqual(FakeDifyHandler.requests[0][0], "/v1/files/upload")
                self.assertEqual(FakeDifyHandler.requests[0][2]["file"], PNG)
                self.assertEqual(FakeDifyHandler.requests[0][2]["user"].decode(),
                                 FakeDifyHandler.requests[1][2]["user"])
                self.assertEqual(FakeDifyHandler.requests[1][2]["files"][0]["upload_file_id"], "http-upload-1")
                request_count = len(FakeDifyHandler.requests)
                self.assertEqual(student.call("/api/agent/message", {
                    "id": session_id, "text": "查看所有图片", "image_ids": [image_id] * 4})[0], 400)
                self.assertEqual(len(FakeDifyHandler.requests), request_count)
                self.assertEqual(student.call("/api/tickets")[1]["tickets"], [])
                self.assertEqual(other.call("/api/agent/message", {
                    "id": session_id, "text": "查看别人的报修"})[0], 403)
                status, follow_up = student.call("/api/agent/message", {
                    "id": session_id, "action": "create_draft"})
                self.assertEqual(status, 200)
                self.assertFalse(follow_up["ready"])
                self.assertEqual(follow_up["missing"], ["灯具是完全不亮还是闪烁？"])
                self.assertIn("仍需师傅", FakeDifyHandler.requests[-1][2]["query"])
                self.assertIn("未解决", follow_up["user_message"])
                self.assertNotIn("宿舍灯不亮", FakeDifyHandler.requests[-1][2]["query"])
                self.assertEqual(student.call("/api/agent/confirm", {"id": session_id})[0], 409)
                status, draft = student.call("/api/agent/message", {
                    "id": session_id, "text": "完全不亮"})
                self.assertEqual(status, 200)
                self.assertTrue(draft["ready"])
                self.assertEqual(draft["draft"]["description"], "灯具完全不亮，需要安排师傅检修。")
                self.assertEqual(draft["draft"]["repair_type"], "light")
                self.assertEqual(FakeDifyHandler.requests[-1][2]["conversation_id"], "http-conversation")
                self.assertEqual(FakeDifyHandler.requests[-1][1], "Bearer app-http-secret")
                recovered = student.call("/api/agent/session?id=" + session_id)[1]
                self.assertNotIn("dify_conversation_id", json.dumps(recovered))
                self.assertNotIn("app-http-secret", json.dumps(recovered))
                self.assertNotIn("http-upload-1", json.dumps(recovered))
                self.assertNotIn("dify_files", json.dumps(recovered))
                self.assertEqual(sum(path.endswith("/files/upload")
                                     for path, _auth, _body in FakeDifyHandler.requests), 1)
                user_turns = [turn for turn in recovered["session"]["history"]
                              if turn["role"] == "user"]
                self.assertEqual(len(user_turns), 3)
                self.assertIn("仍需师傅", user_turns[1]["content"])
                day = student.call("/api/config")[1]["days"][-1]
                status, confirmed = student.call("/api/agent/confirm", {
                    "id": session_id, "date": day, "slots": [[2, 8]],
                    "college": "muse", "dorm": "Z-999"})
                self.assertEqual(status, 200, confirmed)
                self.assertEqual(confirmed["ticket"]["college"], "shaw")
                self.assertEqual(confirmed["ticket"]["dorm"], "A-301")
                self.assertEqual(confirmed["ticket"]["image_ids"], [image_id])
                self.assertEqual(student.call("/api/agent/confirm", {
                    "id": session_id, "date": day, "slots": [[2, 8]]})[1]["ticket"]["id"],
                    confirmed["ticket"]["id"])
                self.assertEqual(len(student.call("/api/tickets")[1]["tickets"]), 1)
                status, extra = student.call("/api/uploads", {
                    "purpose": "agent", "images": [{"mime": "image/png", "data": base64.b64encode(PNG).decode()}]})
                extra_id = extra["uploads"][0]["id"]
                failed_id = student.call("/api/agent/session", {"lang": "zh"})[1]["session"]["id"]
                FakeDifyHandler.fail_uploads = True
                status, failed = student.call("/api/agent/message", {
                    "id": failed_id, "text": "请描述这张照片", "image_ids": [extra_id]})
                self.assertEqual(status, 200)
                self.assertEqual(failed["source"], "dify_error")
                self.assertFalse(failed["ready"])
                self.assertEqual(failed["image_delivery"]["status"], "failed")
                self.assertEqual(FakeDifyHandler.requests[-1][0], "/v1/files/upload")
                FakeDifyHandler.fail_uploads = False
                request_count = len(FakeDifyHandler.requests)
                urgent_id = student.call("/api/agent/session", {"lang": "zh"})[1]["session"]["id"]
                status, urgent = student.call("/api/agent/message", {
                    "id": urgent_id, "action": "create_draft", "text": "插座冒烟"})
                self.assertEqual(status, 200)
                self.assertTrue(urgent["emergency"])
                self.assertTrue(urgent["ready"])
                self.assertEqual(urgent["draft"]["description"], "插座冒烟")
                self.assertEqual(len(FakeDifyHandler.requests), request_count)
                next_id = student.call("/api/agent/session", {"lang": "zh"})[1]["session"]["id"]
                student.call("/api/agent/message", {"id": next_id, "action": "self_help"})
                self.assertIn("安全的自助排查", FakeDifyHandler.requests[-1][2]["query"])
                status, closed = student.call("/api/agent/message", {
                    "id": next_id, "action": "resolved"})
                self.assertEqual(status, 200)
                self.assertEqual(closed["source"], "dify")
                self.assertEqual(closed["stage"], "resolved")
                self.assertIn("已经解决", FakeDifyHandler.requests[-1][2]["query"])
                self.assertEqual(FakeDifyHandler.requests[-1][2]["inputs"]["resolved_after_check"], "yes")
                self.assertTrue(student.call("/api/agent/session?id=" + next_id)[1]["session"]["resolved"])
                self.assertEqual(student.call("/api/agent/message", {
                    "id": next_id, "text": "再次发送"})[0], 409)
                self.assertEqual(len(student.call("/api/tickets")[1]["tickets"]), 1)
            finally:
                if process:
                    process.terminate()
                    process.wait(timeout=10)
                    process._log_handle.close()
                provider.shutdown()
                provider.server_close()
                thread.join(timeout=5)


if __name__ == "__main__":
    unittest.main()
