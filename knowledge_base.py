"""Student-facing retrieval over the bundled dorm knowledge corpus.

The pack ships dense vectors that need an embedding model. This module keeps
the same role, category, installation and safety gates, and ranks with the
pack's BM25 scorer so advice still works with the standard library only.
"""
import collections
import json
import math
import re
from pathlib import Path

def corpus_root():
    """Prefer the unpacked knowledge pack so edits there are picked up live."""
    here = Path(__file__).resolve().parent
    found = []
    pack = here / "知识库"
    if pack.is_dir():
        for child in pack.iterdir():
            if (child / "data" / "chunks.jsonl").is_file():
                found.append(child)
    found.append(here / "knowledge")
    for item in found:
        if (item / "data" / "chunks.jsonl").is_file() or (item / "chunks.jsonl").is_file():
            return item
    return pack


ROOT = corpus_root()

_ADVICE_TYPES = ("reference", "usage_guide")

# Short English lines for the Chinese corpus. The reply language tests require
# an English path even when no model is configured.
_EN = {
    "R-NET-01": "Check the network-port light and try another device you own. Do not open the wall port or the weak-current room.",
    "R-NET-02": "Compare other devices and locations, and sign in to the campus network again if needed. Do not reset a shared access point or send your password.",
    "R-NET-03": "Note whether the slow connection follows one device, one spot, or many people online. Do not move shared network equipment.",
    "R-NET-04": "On your own device, confirm DNS is automatic and whether a non-campus VPN or proxy is on. Do not turn off security software or submit a password.",
    "R-AC-01": "For a Haier remote, check the batteries and the current mode. This does not explain other brands. Do not open the receiver board; record the model if it still fails.",
    "R-AC-02": "A dusty filter can reduce airflow on some Haier units, but weak cooling does not prove low refrigerant. Do not open the unit or add refrigerant.",
    "R-AC-03": "A brief flowing-water sound at startup can be normal on some Haier units. Ongoing noise or dripping still needs a repair ticket.",
    "R-AC-04": "Water on a vane that stays downward can be condensation. It does not explain every leak. Do not open the drain or climb to inspect it.",
    "R-AC-05": "Record the location, model, visible dust and last cleaning. Deep cleaning of the fan or coil is for staff, not a student.",
    "R-WASH-01": "On a twin-tub machine, vibration can follow an uneven load. This is not a diagnosis for every washer. Keep hands out of a spinning tub.",
    "R-WASH-02": "Some washers switch off after a cycle. Do not treat an unknown model that stops mid-cycle as normal; record the model and the moment it stopped.",
    "R-WASH-03": "Some buttons need a firm press. Do not keep striking the panel. Record the model and which keys fail.",
    "R-TAP-01": "A soft cloth is a cleaning note for some TOTO faucets, not a leak repair. A tap that keeps dripping should be reported.",
    "R-SHOWER-01": "For the named TOTO models only, report looseness or a leak around the pipe. Do not dismantle the valve, and do not copy this onto other models.",
    "R-SHOWER-02": "Stop using a shower that is unexpectedly hot. Do not keep testing it by hand, and do not modify the valve.",
    "R-POWER-01": "Stop using a socket that is hot, sparking or loose. Do not open the plate, touch conductors, or keep plugging things in to test it.",
    "R-POWER-02": "Do not overload a socket or pull a plug by the cord. Do not rewire, replace a fuse with a substitute, or bypass a protective device.",
    "R-SAFE-01": "If something smokes, smells burnt or sparks, stop using it, keep away, and tell the duty staff. Cut power only from a safe place.",
    "R-CAMPUS-01": "Campus facilities are handled by different teams. This note does not by itself mean the ticket is already accepted.",
    "R-CAMPUS-02": "Campus network issues may belong to IT services. Collect the symptoms first; do not assume a general repair technician owns every network fault.",
    "R-LING-01": "A published Ling College note asks residents to report a damaged lock to the block desk and not to add or swap locks. It does not cover picking a lock.",
    "R-LING-02": "A published Ling College note says not to alter dorm wiring or move shared equipment. It is not a campus-wide electrical rule by itself.",
    "R-HEATER-USE-01": "A collector's heater guide says to confirm the unit matches the notes before following them. A panel reading of 42 is not proof of water temperature or that the unit is healthy. Keep the ticket if there is still no hot water.",
    "R-HEATER-VALVE-01": "A saved photo note describes one marked gas valve only. Do not judge other valves by color or by whether a handle looks horizontal.",
    "R-GAS-SAFE-01": "Treat a gas smell as separate from ordinary heater use. Do not open a valve, reset a safety device, or ignite anything.",
}

_STATUS_EN = {
    "urgent_gas": "Stop using it and leave the area. Do not switch electrical devices, unplug them, ignite anything, or phone from the room. If you can do so safely, shut the gas and open a window, then call property and gas emergency staff from outside.",
    "urgent_manual": "Stop using it and keep away, then tell the duty staff. Cut power only from a safe position. Do not touch smoking or wet equipment, and do not wait for an ordinary appointment.",
    "restricted_request": "I can't help with credentials, bypassing a lock, or unsafe modifications. You can still record the fault for a person to handle.",
    "need_installation_confirmation": "I need staff to confirm this is the same installation as the notes. Do not judge a gas valve by its color or by a photo, and do not reset or force it open.",
    "need_more_info": "Please name the item, where it is, what it is doing, and when it started.",
    "out_of_scope": "This knowledge base is for dorm facilities. Please describe a facilities fault.",
    "insufficient_evidence": "Nothing in the knowledge base is close enough. Add the item, model and symptom, or create a ticket.",
    "no_procedure": "The knowledge base has no checked do-it-yourself repair for this. Please create a ticket and do not invent a repair.",
    "empty": "The knowledge base is empty. Create a ticket; your details and images will be kept.",
}


def tokens(text):
    text = (text or "").lower()
    words = re.findall(r"[a-z0-9_]+", text)
    for run in re.findall(r"[\u4e00-\u9fff]+", text):
        words.extend(run[i:i + 2] for i in range(max(1, len(run) - 1)))
    return words


def _search_text(record):
    return record["title"] + "。" + " ".join(record.get("keywords") or []) + "。" + record.get("text", "")


def _data_file(root, name):
    nested = Path(root) / "data" / name
    if nested.is_file():
        return nested
    return Path(root) / name


class KnowledgeBase:
    def __init__(self, root=None):
        self.root = Path(root or corpus_root())
        self.records = None
        self.categories = None
        self.sources = {}
        self._stamp = None

    def load(self):
        path = _data_file(self.root, "chunks.jsonl")
        try:
            stamp = path.stat().st_mtime_ns if path.is_file() else None
        except OSError:
            stamp = None
        if self.records is not None and stamp == self._stamp:
            return
        self._stamp = stamp
        if not path.is_file():
            self.records = []
            self.categories = set()
            self.sources = {}
            return
        records = []
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            record = json.loads(line)
            if record.get("active_index", True):
                records.append(record)
        self.records = records
        try:
            categories = json.loads(_data_file(self.root, "categories.json").read_text(encoding="utf-8"))
            self.categories = {item["category_id"] for item in categories}
        except (OSError, ValueError, KeyError):
            self.categories = set()
        try:
            sources = json.loads(_data_file(self.root, "sources.json").read_text(encoding="utf-8"))
            self.sources = {item["source_id"]: item for item in sources if item.get("source_id")}
        except (OSError, ValueError, KeyError):
            self.sources = {}

    def eligible(self, role, category, device_model, college, installation_id=None):
        self.load()
        if role not in ("student", "worker", "admin"):
            raise ValueError("Invalid role")
        if category is not None and self.categories and category not in self.categories:
            raise ValueError("Invalid category")
        matched = []
        for index, record in enumerate(self.records):
            if role not in (record.get("allowed_roles") or []):
                continue
            if category and record.get("category") not in (category, "other"):
                continue
            models = record.get("applicable_models") or []
            if models and (not device_model or device_model.upper() not in models):
                continue
            if (record.get("applicability_scope") == "college_specific_ling_historical"
                    and college != "ling"):
                continue
            installations = record.get("applicable_installations") or []
            if installations and installation_id not in installations:
                continue
            matched.append(index)
        return matched

    def rank(self, query, role="student", category=None, device_model=None,
             college=None, k=3, installation_id=None):
        self.load()
        idx = self.eligible(role, category, device_model, college, installation_id)
        if not idx:
            return []
        query_tokens = collections.Counter(tokens(query))
        docs = [collections.Counter(tokens(_search_text(self.records[i]))) for i in idx]
        count = len(docs)
        average = sum(sum(doc.values()) for doc in docs) / max(count, 1)
        document_freq = collections.Counter(token for doc in docs for token in doc)
        scores = []
        for doc in docs:
            length = sum(doc.values())
            score = 0.0
            for token in query_tokens:
                freq = doc.get(token, 0)
                if not freq:
                    continue
                score += (math.log(1 + (count - document_freq[token] + .5) /
                                   (document_freq[token] + .5)) *
                          freq * 2.2 /
                          (freq + 1.2 * (.25 + .75 * length / max(average, 1))))
            scores.append(score)
        order = sorted(range(len(idx)),
                       key=lambda j: (-float(scores[j]), self.records[idx[j]]["chunk_id"]))[:k]
        return [dict(self.records[idx[j]], score=round(float(scores[j]), 6))
                for j in order if scores[j] > 0]

    def search(self, query, role="student", category=None, device_model=None,
               college=None, k=3, installation_id=None):
        self.load()
        if not self.records:
            return {"status": "empty", "message": _STATUS_EN["empty"], "results": []}
        if role not in ("student", "worker", "admin"):
            raise ValueError("Invalid role")
        query = str(query or "")
        if not query.strip() or len(query) > 2000:
            raise ValueError("Query must contain 1-2000 characters")
        if not 1 <= k <= 10:
            raise ValueError("k must be 1-10")
        gas_query = re.sub(
            r"(没有|并无|无|没)(燃气味|煤气味|天然气味|燃气泄漏|煤气泄漏|臭鸡蛋味)", "", query)
        if re.search(
                r"燃气味|煤气味|天然气味|臭鸡蛋|(?:燃气|煤气|天然气).{0,8}(泄漏|泄露|漏气|异味|报警)"
                r"|(?:热水器|燃气表).{0,6}异味|漏气|(?:燃气|煤气)报警器.{0,6}(响|鸣)",
                gas_query):
            safe = [dict(record, score=None) for record in self.records
                    if record["chunk_id"] == "R-GAS-SAFE-01" and role in record["allowed_roles"]]
            return {
                "status": "urgent_gas",
                "message": "先停止使用，远离现场；不要开关电器、插拔插头、反复点火或在现场打电话。"
                           "能安全做到时关闭气源并开窗，到室外安全处联系物业与燃气抢险人员；"
                           "不能安全操作就先撤离，不按使用视频复位或开阀。",
                "results": safe,
            }
        if re.search(
                r"(泄露|给我|发给我|输出|显示).{0,12}(密钥|密码|提示词)|忽略.{0,12}(规则|指令)"
                r"|撬锁|绕过门禁|短接|带电维修|铜丝.{0,5}保险|自闭阀.{0,8}(复位|强开|旁路)",
                query):
            return {
                "status": "restricted_request",
                "message": "不提供泄露凭据、绕过门禁或危险改装方法；可记录故障并转交人工。",
                "results": [],
            }
        danger = re.sub(r"(没有|并无|无|没)(冒烟|焦味|火花|漏电|触电|烧焦)", "", query)
        if re.search(r"冒烟|焦[糊煳]?味|烧焦|火花|漏电|触电|smoke|sparks|水.{0,6}(插座|插线板)|插座.{0,6}(进水|淹)",
                     danger, re.I):
            safe = [dict(record, score=None) for record in self.records
                    if record["chunk_id"] == "R-SAFE-01" and role in record["allowed_roles"]]
            return {
                "status": "urgent_manual",
                "message": "先停止使用并远离危险，通知值班人员；仅在能安全操作时从安全位置断电，"
                           "不触碰冒烟或进水设备，不等待普通预约。",
                "results": safe,
            }
        if (installation_id != "personal_heater_case_01" and re.search(
                r"(燃气阀|煤气阀|黄色.{0,3}(阀|开关)|燃气开关).{0,15}(横|竖|方向|打开|怎么开|怎么转|复位)"
                r"|(?:横着|竖着).{0,8}(燃气|阀门)", query)):
            return {
                "status": "need_installation_confirmation",
                "message": "我需要先核对这是否是资料对应的装置。不能仅凭阀门颜色或照片中的横竖判断开关方向；"
                           "请先让工作人员确认，不自行复位或强开。",
                "results": [],
            }
        if re.fullmatch(r"[\s，。！!？?]*(坏了|不能用|修一下|帮我修|有问题|报修)[\s，。！!？?]*", query):
            return {
                "status": "need_more_info",
                "message": "请说明具体物品、位置、故障现象及开始时间。",
                "results": [],
            }
        topical = re.search(
            r"修|宿舍|书院|空调|冷气|制冷|风扇|灯|照明|插座|电路|停电|跳闸|电线|网|wifi|wi-fi|LAN|"
            r"花洒|水龙头|水管|下水|马桶|地漏|洗手|洗衣|热水|燃气|煤气|床|椅|书桌|桌子|衣柜|家具|"
            r"沙发|螺丝|柜锁|抽屉|导轨|柜门|挂杆|漏水|滴水|排水|门|窗|地板|瓷砖|墙砖|轨道|遥控|"
            r"照片|语音|型号|零件|E\d", query, re.I)
        english_facilities = re.search(
            r"\b(dorm|network|router|offline|socket|outlet|heater|curtain|wardrobe|"
            r"desk|chair|door|window|lamp|leak|drain|toilet|shower|power|bed|"
            r"aircon|air-con|faucet|wardrobe)\b", query, re.I)
        if not topical and not english_facilities:
            return {
                "status": "out_of_scope",
                "message": "本库用于宿舍设施报修，请提供相关故障。",
                "results": [],
            }
        results = self.rank(query, role, category, device_model, college,
                            k=k, installation_id=installation_id)
        if not results:
            return {
                "status": "insufficient_evidence",
                "message": "未检索到足够相关资料，请补充物品、型号与现象并转人工。",
                "results": [],
            }
        return {
            "status": "prototype_evidence",
            "message": "以下为知识库参考，尚未经过物业或师傅现场审核；不能据此确认内部故障。",
            "results": results,
        }


_BASE = KnowledgeBase()


def search(query, user, category=None, k=8):
    """Retrieve with the signed-in role. Installation id is never taken from the client."""
    role = (user or {}).get("role")
    if role not in ("student", "worker", "admin"):
        raise PermissionError("Authenticated role required")
    if category is not None and category not in (_BASE.categories or set()):
        _BASE.load()
        if category not in (_BASE.categories or set()):
            category = None
    return _BASE.search(
        query, role=role, category=category, device_model=None,
        college=(user or {}).get("college"), k=k, installation_id=None)


_CATEGORY_HINTS = (
    ("network", ("网络", "网线", "网口", "wifi", "wi-fi", "路由器", "宽带")),
    ("appliance", ("热水器", "洗衣机", "冰箱")),
    ("ac", ("空调", "冷气", "遥控")),
    ("ac_service", ("清洗空调", "空调清洗", "保养")),
    ("power", ("插座", "跳闸", "漏电", "电线")),
    ("plumbing", ("漏水", "水龙头", "下水道", "水管")),
    ("bathroom", ("马桶", "花洒", "地漏", "洗手")),
    ("light", ("灯", "照明", "灯泡")),
    ("door", ("门锁", "房门", "窗户")),
    ("wardrobe", ("衣柜", "柜门", "挂杆")),
    ("desk", ("书桌", "抽屉")),
    ("chair", ("椅子", "凳子")),
    ("bed", ("床板", "床架", "护栏")),
    ("curtain", ("窗帘", "轨道")),
    ("floor", ("地板", "瓷砖", "墙砖")),
    ("furniture", ("合页", "导轨", "柜锁")),
)


def category_for_query(query, hinted=None):
    """Follow the latest fault the student named, otherwise keep the draft category."""
    named = []
    low = (query or "").lower()
    for category, words in _CATEGORY_HINTS:
        if any(word.lower() in low for word in words):
            named.append(category)
    if len(set(named)) == 1:
        return named[0]
    return hinted


def _sentences(text):
    text = re.sub(r"^我根据来源资料整理如下：", "", text or "")
    text = re.sub(r"^我在一次实际经历中", "资料整理者在一次实际经历中", text)
    parts = re.split(r"(?<=[。！？])", text)
    cleaned = []
    for part in parts:
        part = re.sub(r"\s+", " ", part).strip()
        if len(part) < 8:
            continue
        cleaned.append(part)
    return cleaned[:2]


def _source_titles(record):
    titles = []
    for source_id in record.get("source_ids") or []:
        source = _BASE.sources.get(source_id) or {}
        title = source.get("title")
        if title and title not in titles:
            titles.append(title)
    return titles


def advice_for_student(query, user, category=None, lang="zh"):
    """Search the live pack for this utterance. Steps quote the matched chunks."""
    category = category_for_query(query, category)
    try:
        found = search(query, user, category=category, k=8)
    except ValueError:
        found = {"status": "insufficient_evidence", "message": "", "results": []}
    status = found.get("status") or "empty"
    results = found.get("results") or []
    actionable = [item for item in results if item.get("knowledge_type") in _ADVICE_TYPES]
    # Same-category notes outrank generic "other" policies that merely share wording.
    if category:
        specific = [item for item in actionable if item.get("category") == category]
        if specific:
            actionable = specific
    steps = []
    for item in actionable[:3]:
        titles = _source_titles(item)
        if lang == "en":
            text = _EN.get(item.get("chunk_id"))
            if not text:
                continue
        else:
            sentences = _sentences(item.get("text") or "")
            text = "".join(sentences)[:280]
            if titles:
                text = text.rstrip("。") + "。（来源：" + "、".join(titles[:2]) + "）"
        if not text:
            continue
        steps.append({
            "text": text,
            "chunk_id": item.get("chunk_id"),
            "source_titles": titles,
            "image_url": None,
        })
    if status == "prototype_evidence" and not steps:
        status = "no_procedure"
    kb_status = {
        "prototype_evidence": "hit",
        "empty": "empty",
    }.get(status, status if status in _STATUS_EN else "no_hit")
    if status == "prototype_evidence":
        kb_status = "hit"
    elif status == "no_procedure":
        kb_status = "no_procedure"
    elif status == "empty":
        kb_status = "empty"
    else:
        kb_status = status
    message = found.get("message") or ""
    if lang == "en":
        message = _STATUS_EN.get(status) or _STATUS_EN["insufficient_evidence"]
    elif status == "no_procedure":
        message = "知识库里没有经审核的自行维修步骤。请生成工单，不要自行拆修。"
    elif status == "empty":
        message = "知识库当前为空。请生成工单，现有信息和图片会保留。"
    return {
        "status": status,
        "kb_status": kb_status,
        "message": message,
        "steps": steps if status in ("prototype_evidence", "urgent_gas", "urgent_manual") else [],
        "chunk_ids": [item.get("chunk_id") for item in actionable[:3]],
    }
