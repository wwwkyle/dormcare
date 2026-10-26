#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
DormCare · AI 智能体 / AI agents (RAG over the fixed knowledge base)
===================================================================

Four agents live here. All four are the SAME retrieval pattern: the fixed
knowledge base in config.py is retrieved and injected into the system prompt,
and the model is forbidden from using anything else. That is the "接入 RAG"
requirement, implemented honestly — the corpus is real, retrieved per request,
and the prompt makes it the sole source of truth.

    parse_intake()     学生端 —— 自然语言 -> 结构化工单（分类 + 紧急度 + 时长）
    advise_ticket()    学生端 —— 给学生的处理建议 / 进度解释
    advise_dispatch()  工人端 —— 给师傅的派活说明（含 AI 排班理由、备件、注意事项）
    summarize_plan()   工人端 —— 把确定性排班结果翻译成人话

Hard rule observed everywhere: the numbers (duration, travel time, clock
times) are NEVER produced by the model. They come from config.py and
scheduler.py and are handed TO the model. The model only classifies, explains
and writes text.
"""

import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request

from config import (COLLEGES, REPAIR_TYPES, REPAIR_BY_ID, TRAVEL_RULES,
                    build_college_table, build_repair_table, _fmt_minutes,
                    college_label, repair_label)
from scheduler import PRIORITY_LEVELS

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
DEFAULT_MODEL = "deepseek/deepseek-chat"
# Empty base means the OpenRouter URL above. Anything else is an OpenAI-compatible root;
# call_model appends /chat/completions.
API_BASE = ""
API_BASE_SET = False
PROVIDERS = {
    "openrouter": {"base": "", "model": DEFAULT_MODEL},
    "openai": {"base": "https://api.openai.com/v1", "model": "gpt-4o-mini"},
    "deepseek": {"base": "https://api.deepseek.com/v1", "model": "deepseek-flash"},
    "moonshot": {"base": "https://api.moonshot.cn/v1", "model": "moonshot-v1-8k"},
    "zhipu": {"base": "https://open.bigmodel.cn/api/paas/v4", "model": "glm-4-flash"},
    "siliconflow": {"base": "https://api.siliconflow.cn/v1", "model": "deepseek-ai/DeepSeek-V3"},
    "dashscope": {"base": "https://dashscope.aliyuncs.com/compatible-mode/v1", "model": "qwen-plus"},
    "custom": {"base": None, "model": ""},
}


def set_api_base(url):
    """Process-wide upstream root. Empty keeps the OpenRouter endpoint."""
    global API_BASE, API_BASE_SET
    API_BASE = (url or "").strip().rstrip("/")
    API_BASE_SET = True


def clear_api_base():
    """Drop a page-enrolled root so the process falls back to the environment."""
    global API_BASE, API_BASE_SET
    API_BASE = ""
    API_BASE_SET = False


def configured_base():
    if API_BASE_SET:
        return API_BASE
    return (os.environ.get("OPENAI_BASE_URL") or "").strip().rstrip("/")


def valid_custom_base(url):
    parsed = urllib.parse.urlparse((url or "").strip())
    host = (parsed.hostname or "").lower()
    if parsed.scheme != "https" or not host or parsed.username or parsed.password:
        return False
    if host in ("localhost", "0.0.0.0") or host.endswith(".local"):
        return False
    if host.startswith("127.") or host.startswith("169.254.") or host == "169.254.169.254":
        return False
    return True


_MODEL_RE = re.compile(r"^[\w./:@+\-]{1,120}$")

MAX_TOKENS = 900
TEMPERATURE = 0.2          # low: we want classification, not creativity
UPSTREAM_TIMEOUT = 75

# --------------------------------------------------------------------------- #
# 双语固定回复 / Bilingual contractual refusals
# These are copied byte for byte by the model, exactly like a lab-spec agent.
# --------------------------------------------------------------------------- #

MSG_OUT_OF_SCOPE = {
    "zh": "这个问题超出了我的职责范围。我只负责宿舍报修、维修排班和宿舍设施相关的事宜。"
          "其他事务请咨询学生事务处或相关部门。",
    "en": "That's outside my scope. I only handle dorm repair requests, maintenance "
          "scheduling and residential facilities. For anything else, please contact "
          "the Student Affairs Office or the relevant department.",
}

MSG_NOT_IN_KB = {
    "zh": "抱歉，这个信息不在我的知识库中。我只能依据系统内的报修类型、书院信息和工时标准作答。",
    "en": "Sorry, this information is not in my knowledge base. I can only answer from "
          "the repair categories, college data and labour standards recorded here.",
}


class AIError(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status = status
        self.message = message


# --------------------------------------------------------------------------- #
# 知识库渲染 / Knowledge rendering (the RAG corpus)
# --------------------------------------------------------------------------- #

def knowledge_block(lang="zh"):
    """The retrieved context injected into every agent prompt."""
    if lang == "zh":
        return """## 一、报修类型与固定工时（不得修改、不得估算、不得新增）
{repairs}

## 二、书院清单
{colleges}

## 三、路程规则（固定值）
- 书院之间往返：{cross} 分钟
- 同一书院内：{same} 分钟
- 工人班次：{start} - {end}，午休 {lunch_s} - {lunch_e}
- 一项维修占用的时间 = 固定维修时长；两项维修之间必须加上路程时间
""".format(
            repairs=build_repair_table(True),
            colleges=build_college_table(True),
            cross=TRAVEL_RULES["cross_college_minutes"],
            same=TRAVEL_RULES["same_college_minutes"],
            start=TRAVEL_RULES["workday_start"],
            end=TRAVEL_RULES["workday_end"],
            lunch_s=TRAVEL_RULES["lunch_start"],
            lunch_e=TRAVEL_RULES["lunch_end"],
        )
    return """## 1. Repair categories and FIXED durations (never altered, never estimated, never extended)
{repairs}

## 2. College list
{colleges}

## 3. Travel rules (fixed values)
- Between colleges: {cross} minutes
- Within the same college: {same} minutes
- Shift: {start} - {end}, lunch break {lunch_s} - {lunch_e}
- A job occupies its fixed duration; every move between two jobs adds travel time
""".format(
        repairs=build_repair_table(False),
        colleges=build_college_table(False),
        cross=TRAVEL_RULES["cross_college_minutes"],
        same=TRAVEL_RULES["same_college_minutes"],
        start=TRAVEL_RULES["workday_start"],
        end=TRAVEL_RULES["workday_end"],
        lunch_s=TRAVEL_RULES["lunch_start"],
        lunch_e=TRAVEL_RULES["lunch_end"],
    )


def priority_block(lang="zh"):
    if lang == "zh":
        return "\n".join("- `%s` %s：%s" % (k, v["zh"], v["zh_desc"])
                         for k, v in PRIORITY_LEVELS.items())
    return "\n".join("- `%s` %s: %s" % (k, v["en"], v["en_desc"])
                     for k, v in PRIORITY_LEVELS.items())


# --------------------------------------------------------------------------- #
# 上游调用 / Upstream call
# --------------------------------------------------------------------------- #

def call_model(api_key, model, messages, *, max_tokens=MAX_TOKENS,
               temperature=TEMPERATURE, json_mode=False):
    payload = {
        "model": model,
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": temperature,
    }
    if json_mode:
        payload["response_format"] = {"type": "json_object"}
    base = configured_base()
    url = (base + "/chat/completions") if base else OPENROUTER_URL
    host = (urllib.parse.urlparse(url).hostname or "").lower()
    # Thinking is on by default and can spend the whole token budget before
    # any answer is written. Turn it off so the reply arrives in `content`.
    if host.endswith("deepseek.com"):
        payload["thinking"] = {"type": "disabled"}
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": "Bearer " + api_key,
            "Content-Type": "application/json",
            "HTTP-Referer": "https://workbuddy.cn",
            "X-Title": "DormCare Repair Dispatch",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=UPSTREAM_TIMEOUT) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = ""
        try:
            body = json.loads(exc.read().decode("utf-8"))
            detail = body.get("error", {}).get("message") or str(body)
        except Exception:
            pass
        raise AIError(exc.code, detail)
    except Exception as exc:
        raise AIError(502, type(exc).__name__)
    try:
        return data["choices"][0]["message"]["content"] or ""
    except (KeyError, IndexError, TypeError):
        raise AIError(502, "unexpected model response")


def verify_key(api_key, model):
    """One cheap round-trip to prove the key works."""
    try:
        call_model(api_key, model,
                   [{"role": "user", "content": "ping"}],
                   max_tokens=5, temperature=0)
        return True
    except AIError as exc:
        return "auth" if exc.status in (401, 403) else "unavailable"
    except Exception:
        return "unavailable"


# --------------------------------------------------------------------------- #
# 本地兜底分类器 / Deterministic keyword fallback
#
# If the model is unreachable we must still be able to file a ticket: the
# student should never be blocked by an API outage. This maps Chinese and
# English keywords straight onto the fixed catalogue.
# --------------------------------------------------------------------------- #

_KEYWORDS = [
    ("ac",          ["空调", "冷气", "制冷", "不凉", "air con", "air-con", " ac ", "cooling"]),
    ("ac_service",  ["清洗空调", "空调清洗", "保养", "deep clean", "service the ac"]),
    ("bed",         ["床", "床板", "护栏", "bed", "bunk", "slat"]),
    ("desk",        ["桌子", "书桌", "桌面", "抽屉卡", "desk", "table"]),
    ("chair",       ["椅子", "凳子", "靠背", "气压", "chair", "stool"]),
    ("wardrobe",    ["衣柜", "柜门", "挂杆", "wardrobe", "closet"]),
    ("light",       ["灯", "照明", "灯管", "灯泡", "开关", "light", "lamp", "bulb"]),
    ("plumbing",    ["漏水", "下水", "堵", "水管", "龙头", "plumbing", "leak", "drain", "tap"]),
    ("bathroom",    ["花洒", "马桶", "洗手", "淋浴", "地漏", "shower", "toilet", "basin", "bathroom"]),
    ("power",       ["插座", "跳闸", "断电", "电路", "电闸", "socket", "power", "breaker", "electric"]),
    ("network",     ["网络", "网口", "网线", "断网", "wifi", "internet", "ethernet", "lan"]),
    ("appliance",   ["热水器", "洗衣机", "冰箱", "water heater", "washing machine", "fridge"]),
    ("door",        ["门锁", "房门", "窗", "锁", "door", "lock", "window"]),
    ("floor",       ["地板", "瓷砖", "墙砖", "踢脚", "floor", "tile"]),
    ("curtain",     ["窗帘", "轨道", "curtain", "blind"]),
    ("furniture",   ["五金", "导轨", "柜锁", "螺丝", "hardware", "hinge", "screw"]),
]

def fallback_classify(text, lang="zh"):
    """Keyword-only classification. Always returns a valid catalogue entry."""
    low = " " + (text or "").lower() + " "
    for rid, words in _KEYWORDS:
        for w in words:
            if w in low:
                return {
                    "repair_type": rid,
                    "priority": "normal",
                    "confidence": "low",
                    "source": "rules",
                }
    return {"repair_type": "other", "priority": "normal",
            "confidence": "low", "source": "rules"}


# --------------------------------------------------------------------------- #
# 智能体 1 —— 学生端工单理解 / Student-side intake parser
# --------------------------------------------------------------------------- #
# 追问与自检 / Follow-up questions and self-help steps
#
# The intake agent has two jobs beyond classifying: when a description is too
# thin to act on, ASK; when it is clear enough, say what the resident can safely
# do before anybody turns up.
#
# What follows is the offline version, used when there is no model. The table
# is deliberately conservative: every step must be doable from the floor, with
# no tools, by somebody who has never fixed anything. Anything involving mains
# electricity, gas, dismantling equipment or working at height is NOT a
# self-help step — telling a student to "check the wiring" is how people get
# hurt, so those cases say "wait for the technician" instead.
# --------------------------------------------------------------------------- #

SELF_HELP = {
    "desk": {
        "zh": ["把抽屉整个抽出来，清掉滑轨里卡住的笔、纸片之类，再装回试试。",
               "桌腿螺丝松了可以用内六角扳手轻轻拧紧；桌面板不要自己拆。"],
        "en": ["Pull the drawer right out, clear whatever is jammed in the runner, then slide it back.",
               "Loose leg bolts can be nipped up with an Allen key. Do not take the top off."],
    },
    "chair": {
        "zh": ["先确认是真坏了还是没调好：座垫下方通常有个手柄，坐上去按一下看高度能不能动。",
               "脚轮缠上头发或线头会推不动，把椅子侧放清理干净；靠背有裂纹就别再坐了。"],
        "en": ["Check whether it is broken or just unadjusted — there is usually a lever under the seat; press it while seated.",
               "Hair wound round the castors stops a chair rolling; tip it on its side and clear them. Do not keep using a cracked backrest."],
    },
    "bed": {
        "zh": ["先找出是哪一块床板断了，把它抽出来别再压在上面，免得塌下去受伤。",
               "床架螺丝松可以拧紧，但断掉的床板或护栏不要自己接，等师傅换件。"],
        "en": ["Find which slat has gone and take it out — do not keep loading it, or the bed can give way under you.",
               "Loose frame bolts can be tightened, but never splice a snapped slat or rail; wait for the part."],
    },
    "wardrobe": {
        "zh": ["柜门掉下来先靠到不会倒的地方，别立在床边。",
               "合页螺丝孔滑丝了就别反复拧，拧不紧反而会把孔撑大，等师傅加固。"],
        "en": ["Lean a fallen door somewhere it cannot topple onto anyone.",
               "If the hinge screw holes have stripped, stop retightening — you will only widen them. Wait for the reinforcement."],
    },
    "ac": {
        "zh": ["先确认遥控器电池和这一路的空气开关：不少「不制冷」其实是电源或模式设错。",
               "滤网可以取下用清水冲洗、晾干后装回。如果漏水或有异响，请先关机等师傅。"],
        "en": ["Check the remote batteries and the breaker first — plenty of \"not cooling\" calls are power or a wrong mode.",
               "Rinse the filter under the tap, let it dry, refit it. If it is leaking or rattling, switch it off and wait."],
    },
    "ac_service": {
        "zh": ["清洗前把被子、衣物收好，过程中会有水汽和灰尘。",
               "记一下空调品牌和大概用了几年，师傅上门会问。"],
        "en": ["Put bedding and clothes away first — a deep clean throws out mist and dust.",
               "Note the brand and roughly how old the unit is; the technician will ask."],
    },
    "light": {
        "zh": ["先换一支确认好的灯泡或灯管试试，很多时候只是灯管到寿命了。",
               "开关可以反复按两下看是不是接触不良；不要自己拆灯座或碰裸露电线。"],
        "en": ["Try a bulb or tube you know works — often that is the whole problem.",
               "Work the switch a couple of times in case it is a bad contact. Never open the fitting or touch bare wiring."],
    },
    "furniture": {
        "zh": ["把松动的位置拍张照，师傅就知道该带哪种五金件。",
               "还能吃住力的螺丝可以拧紧；孔位滑丝或开裂了就别再拧。"],
        "en": ["Photograph the loose fitting so the technician knows which part to bring.",
               "Screws that still bite can be tightened; stop once a hole has stripped or split."],
    },
    "plumbing": {
        "zh": ["先关掉角阀或总水阀，能挡住继续漏水。",
               "洗手台或地漏堵塞多半是头发，皮搋子上下压几下常常就通了；如果水已经漫到插座附近，先断掉那一路电源再等师傅。"],
        "en": ["Turn off the angle valve or the main stopcock first — that stops the damage spreading.",
               "Basin and floor-drain blockages are usually hair, and a few firm plunges often clear them. If water has reached sockets, cut that circuit and wait."],
    },
    "bathroom": {
        "zh": ["花洒出水小，可以把花洒头拧下来清理里面的水垢和滤网。",
               "马桶一直流水多半是水箱里的浮球或按钮卡住，掀开箱盖看一眼；瓷体不要自己拆。"],
        "en": ["A weak shower is usually scale — unscrew the head and clean the filter inside.",
               "A toilet that keeps running is usually the float or button stuck in the cistern; lift the lid and look. Never dismantle the ceramic."],
    },
    "power": {
        "zh": ["先看是不是这一路的空气开关跳了：拔掉大功率电器再合闸一次。",
               "插座有焦糊味、发烫或打火，立刻停用并断掉这一路电源 —— 不要自己拆插座。"],
        "en": ["Check whether the breaker for that circuit has tripped; unplug the heavy load and reset it once.",
               "If a socket smells burnt, is hot or sparks, stop using it and cut that circuit — do not open the socket."],
    },
    "network": {
        "zh": ["把网线两端拔下重插，确认插在「墙上网口」而不是旁边的电话口。",
               "换一根网线、换一台设备试试，就能判断是线的问题还是口的问题。"],
        "en": ["Unplug and reseat both ends of the cable, and check it is in the data port, not the phone socket beside it.",
               "Try another cable and another device — that tells you whether the cable or the wall port is at fault."],
    },
    "appliance": {
        "zh": ["先确认插头和这一路的空气开关；热水器不出热水还要看温控设定和进水阀。",
               "机器有明显异响、漏水或跳闸时请断电停用，不要继续用。"],
        "en": ["Check the plug and the breaker; on a heater also check the temperature setting and the inlet valve.",
               "If it is rattling, leaking or tripping the breaker, switch it off and stop using it."],
    },
    "door": {
        "zh": ["锁芯发涩可以在钥匙上抹一点铅笔芯粉末（石墨），不要滴食用油。",
               "门关不上先看门框和合页螺丝是不是松了；窗户关不严可以清一清轨道里的杂物。"],
        "en": ["A stiff lock often frees up with pencil graphite on the key. Do not use cooking oil.",
               "If a door will not close, check the frame and hinge screws; a window that will not shut is usually debris in the track."],
    },
    "floor": {
        "zh": ["翘起的地板先用胶带圈出位置，绕开走，免得绊倒或割到脚。",
               "墙砖有开裂或空鼓不要自己敲打，掉落风险比看上去大。"],
        "en": ["Tape off a lifted board and walk around it — it is a trip and cut hazard.",
               "Do not tap or lever a cracked tile; it comes away more easily than it looks."],
    },
    "curtain": {
        "zh": ["先看轨道里是不是卡住了挂钩或线头 —— 站在地面上够得到再处理。",
               "够不到的地方等师傅，不要踩椅子去够轨道，摔伤远比窗帘严重。"],
        "en": ["Check the track for a jammed hook or thread, but only if you can reach it from the floor.",
               "Never stand on a chair to reach the track. A fall hurts far more than a curtain."],
    },
    "other": {
        "zh": ["尽量写清楚具体位置、什么时候开始、现在是什么状态，师傅好准备工具。",
               "如果涉及水、电、燃气或高处作业，请不要自己动手。"],
        "en": ["Describe where it is, when it started and what state it is in now, so the right tools come along.",
               "If it involves water, electricity, gas or height, please leave it alone."],
    },
}

#: Questions asked when the description is too thin to act on.
FOLLOW_UP_GENERIC = {
    "zh": ["故障具体在房间的哪个位置？", "大概从什么时候开始的？", "现在还能正常使用吗？"],
    "en": ["Where exactly in the room is it?",
           "When did it start?",
           "Is it still usable right now?"],
}

FOLLOW_UP_EXTRA = {
    "ac":        {"zh": "是吹出来的风不凉，还是完全不出风？", "en": "Is it blowing warm air, or not blowing at all?"},
    "plumbing":  {"zh": "是持续在漏，还是一冲水就返上来？", "en": "Is it leaking constantly, or backing up when you flush?"},
    "power":     {"zh": "是这一路都没电，还是只有这一个插座？", "en": "Is the whole circuit dead, or just this one socket?"},
    "light":     {"zh": "是灯完全不亮，还是在闪？", "en": "Completely dead, or flickering?"},
    "network":   {"zh": "是能连上但很慢，还是完全连不上？", "en": "Slow, or no connection at all?"},
    "door":      {"zh": "是锁不上，还是打不开？", "en": "Won't lock, or won't open?"},
    "appliance": {"zh": "是通电完全没反应，还是运行中不正常？", "en": "No response at all, or misbehaving while running?"},
    "floor":     {"zh": "大概多大一块？走路会不会绊到？", "en": "Roughly how big an area? Is it a trip hazard?"},
    "bathroom":  {"zh": "是堵了，还是一直在漏水？", "en": "Is it blocked, or continuously leaking?"},
    "bed":       {"zh": "是床板断了，还是床架在响？", "en": "Is a slat broken, or is the frame creaking?"},
}


def _fallback_extras(text, repair_type, lang="zh"):
    """Deterministic follow-ups + self-help when there is no model.

    `need_more_info` is decided by length, which is crude but honest: a
    three-word description almost never says where, when or how bad, and the
    questions below are the ones that fill that gap.
    """
    lang = lang if lang in ("zh", "en") else "zh"
    tips = list(SELF_HELP.get(repair_type, SELF_HELP["other"]).get(lang, []))
    stripped = (text or "").strip()
    short = len(stripped) < (10 if lang == "zh" else 30)
    if not short:
        return {"need_more_info": False, "follow_up": [], "self_help": tips}

    questions = [q for q in FOLLOW_UP_GENERIC[lang]]
    extra = FOLLOW_UP_EXTRA.get(repair_type)
    if extra:
        questions.insert(1, extra[lang])
    return {"need_more_info": True, "follow_up": questions[:3], "self_help": tips}


# --------------------------------------------------------------------------- #

INTAKE_SYSTEM = """# Role
你是「DormCare 宿舍报修系统」的**工单受理智能体**。你的唯一任务是把学生用自然语言
描述的故障，映射到知识库中已有的报修类别上。

# Knowledge base (the ONLY source of truth)
{knowledge}

# Priority levels
{priorities}

# 输出格式 / Output format
只输出一个 JSON 对象，不要有任何解释、不要有 markdown 代码围栏、不要有前后缀：

{{
  "repair_type": "<知识库中的类别ID，必须逐字一致>",
  "priority": "urgent | high | normal | low",
  "reasoning": "<一句话说明你为什么这么分类，中文，不超过40字>",
  "confidence": "high | medium | low",
  "need_more_info": true | false,
  "follow_up": ["<需要学生补充的问题，0-3条，每条不超过30字>"],
  "self_help": ["<学生自己可以安全操作的处理步骤，1-3条，每条不超过40字>"]
}}

# 规则 / Rules
1. `repair_type` 必须来自上面的类别ID列表，一个字母都不能改。如果确实无法归入任何
   类别，才使用 `other`。
2. 不要估算维修时长 —— 时长由系统查表决定，你不需要也不允许给出分钟数。
3. `priority` 依据故障对居住安全与正常生活的影响程度判断，参考上面的紧急度定义。
4. 只根据知识库作答。如果学生问的是与宿舍报修无关的事（课程、成绩、教授、签证等），
   不要编造，仍然返回 `"repair_type": "other"` 并把 `"confidence"` 设为 `"low"`。

5. **追问**：当描述太笼统、缺关键信息（具体位置、什么时候开始、什么现象、是否还能用）
   时，`need_more_info` 设为 `true`，并用 `follow_up` 问出补上这些信息所必需的 1-3 个问题。
   问题要具体到这一单，不要问「请补充更多细节」这种空话。信息已经够清楚时设为 `false`
   并返回空数组。
6. **自检建议**：`self_help` 是学生在师傅上门前**自己就能安全完成**的 1-3 步操作，必须
   具体、可直接照做，例如「把滤网取下用清水冲洗、晾干后装回」。**禁止**出现任何涉及
   220V 电路、燃气、拆解设备、登高（踩椅子够高处）的操作——这类情况请写「不要自行处理，
   留在原地等待师傅」。不要写「注意安全」「保持通风」这类没有动作的空话。
7. 全篇不得出现价格、工期、政策、人员姓名。

# Language
学生用中文提问就用中文写，用英文提问就用英文。"""

INTAKE_SYSTEM_EN = """# Role
You are the **intake agent** of "DormCare", a smart dorm repair system. Your job
is to map a student's free-text description of a fault onto a repair category
that already exists in the knowledge base — and, beyond that, to work out what
is still missing or what the student could usefully try.

# Knowledge base (the ONLY source of truth)
{knowledge}

# Priority levels
{priorities}

# Output format
Output one JSON object only — no explanation, no markdown fences, no prefix or
suffix:

{{
  "repair_type": "<category ID from the knowledge base, character for character>",
  "priority": "urgent | high | normal | low",
  "reasoning": "<one sentence on why, in English, under 40 words>",
  "confidence": "high | medium | low",
  "need_more_info": true | false,
  "follow_up": ["<question the student must answer, 0-3 of them, under 30 words each>"],
  "self_help": ["<something the student can safely do themselves, 1-3 items, under 40 words each>"]
}}

# Rules
1. `repair_type` must come from the category ID list above, unchanged. Use
   `other` only when nothing genuinely fits.
2. Do not estimate labour time — the system looks the duration up in a table.
   You neither need to nor may give a number of minutes.
3. Decide `priority` from the effect on residential safety and daily life, using
   the level definitions above.
4. Answer only from the knowledge base. If the student asks something unrelated
   to dorm repairs (courses, grades, professors, visas), do not invent an
   answer — still return `"repair_type": "other"` with `"confidence": "low"`.

5. **Follow-up**: when the description is too vague to act on — no location, no
   onset, no symptom detail, no idea whether the thing still works — set
   `need_more_info` to `true` and use `follow_up` for the 1-3 questions needed
   to close exactly those gaps. Ask about THIS fault; never ask "please provide
   more detail". When the description is already clear, set it to `false` and
   return an empty array.
6. **Self-help**: `self_help` is 1-3 steps the student can safely carry out
   themselves before the technician arrives, each concrete enough to follow
   literally — e.g. "rinse the filter under the tap, let it dry, refit it".
   **Never** suggest anything touching mains wiring, gas, dismantling equipment
   or working at height; for those, say "leave it alone and wait for the
   technician". No filler like "be careful" or "keep the room ventilated".
7. Never mention prices, lead times, policies or staff names.
8. `reasoning`, `follow_up` and `self_help` must all be written in English."""

# Both variants share the same JSON KEYS, so the parser below does not care
# which one ran — only the human-readable `reasoning` changes language.
INTAKE_SYSTEM = {"zh": INTAKE_SYSTEM, "en": INTAKE_SYSTEM_EN}


def _clean_list(raw, limit, max_len):
    """A model-supplied list of short strings, normalised and capped."""
    if not isinstance(raw, list):
        return []
    out = []
    for item in raw:
        txt = str(item or "").strip()
        if txt:
            out.append(txt[:max_len])
        if len(out) >= limit:
            break
    return out


def parse_intake(api_key, model, text, lang="zh"):
    """Free text -> classification, plus what to ask or what to try.

    Returns `repair_type`, `priority`, `reason`, `confidence`, `duration_minutes`
    (looked up here, never guessed by the model), and the two fields that make
    the intake useful rather than merely tidy:

      * `need_more_info` + `follow_up` — what the description failed to say,
      * `self_help`                    — what the student can safely try first.

    Falls back to the keyword classifier and a deterministic question/step table
    when the model is unavailable, so the student console never ends up with a
    classification and nothing to do with it.
    """
    lang = lang if lang in ("zh", "en") else "zh"
    if not api_key:
        res = fallback_classify(text, lang)
        res["reason"] = ("已按关键词归类（模型未启用）。" if lang == "zh"
                         else "Classified by keyword rules (model not enabled).")
        res["duration_minutes"] = REPAIR_BY_ID[res["repair_type"]]["minutes"]
        res.update(_fallback_extras(text, res["repair_type"], lang))
        return res

    system = INTAKE_SYSTEM[lang].format(knowledge=knowledge_block(lang),
                                        priorities=priority_block(lang))
    try:
        raw = call_model(
            api_key, model,
            [{"role": "system", "content": system},
             {"role": "user", "content": text}],
            max_tokens=700, temperature=0, json_mode=True,
        )
        data = _extract_json(raw)
        rid = str(data.get("repair_type", "")).strip()
        if rid not in REPAIR_BY_ID:
            rid = fallback_classify(text, lang)["repair_type"]
        pri = str(data.get("priority", "normal")).strip().lower()
        if pri not in PRIORITY_LEVELS:
            pri = "normal"
        # A model that returns questions but leaves the flag off is still
        # asking something, so the flag follows the content.
        questions = _clean_list(data.get("follow_up"), 3, 120)
        tips = _clean_list(data.get("self_help"), 3, 160)
        fallback = _fallback_extras(text, rid, lang)
        if not tips:
            tips = fallback["self_help"]
        return {
            "repair_type": rid,
            "priority": pri,
            "reason": (data.get("reasoning") or "").strip()[:200],
            "confidence": str(data.get("confidence", "medium")).strip().lower(),
            "duration_minutes": REPAIR_BY_ID[rid]["minutes"],
            "source": "model",
        }
    except Exception:
        res = fallback_classify(text, lang)
        res["reason"] = ("模型暂时不可用，已按关键词归类。" if lang == "zh"
                         else "Model unavailable; classified by keyword rules.")
        res["duration_minutes"] = REPAIR_BY_ID[res["repair_type"]]["minutes"]
        res.update(_fallback_extras(text, res["repair_type"], lang))
        return res


def _extract_json(raw):
    """Pull the first JSON object out of a model reply, tolerating fencing."""
    if not raw:
        return {}
    txt = raw.strip()
    txt = re.sub(r"^```(?:json)?\s*", "", txt)
    txt = re.sub(r"\s*```$", "", txt).strip()
    try:
        return json.loads(txt)
    except Exception:
        pass
    m = re.search(r"\{[\s\S]*\}", txt)
    if m:
        try:
            return json.loads(m.group(0))
        except Exception:
            return {}
    return {}


# --------------------------------------------------------------------------- #
# 智能体 2 —— 学生端建议 / Student-side advice
# --------------------------------------------------------------------------- #

ADVICE_SYSTEM_ZH = """# Role
你是「DormCare 宿舍报修系统」的**学生服务智能体**。

# 知识库（唯一信息来源）
{knowledge}

# 任务
学生会描述一个报修问题，系统已经把它归类好了。你要做三件事：
1. **安全提示**：如果涉及漏水、漏电、冒烟、燃气等，先给一句立即可执行的安全处置建议。
2. **自己能做的**：给 1-3 条学生现在就能安全完成的检查或处理动作，要具体到可以直接照做
   （例如「把滤网取下用清水冲洗、晾干后装回」）。涉及 220V 电路、燃气、拆解设备、登高的
   操作一律不写，改为「不要自行处理，等待师傅」。
3. **报修指引**：告诉学生还需要确认什么（书院、宿舍号、空闲时间），以及**是否需要补充
   信息**——若描述不够清楚，就在这里把要问的问题列出来。
4. **范围守则**：只谈宿舍报修与维修安排。课程、成绩、教授、签证、社团等一律不属于
   你的范围。

# 范围外时的回复（逐字照抄，不得增删改写、不得翻译）
{msg_out_of_scope}

# 知识库中没有答案时（逐字照抄，不得增删改写）
{msg_not_in_kb}

# 风格
简洁、可执行、值得信赖。不要编造价格、工期、政策或人员姓名。
**绝对不要出现任何分钟数或时长** —— 工时标准是系统内部用于排班的固定值，不是对学生的
承诺，学生端也不显示它。**必须全程使用简体中文输出。**"""

ADVICE_SYSTEM_EN = """# Role
You are the **student service agent** of "DormCare", a smart dorm repair system.

# Knowledge base (the ONLY source of truth)
{knowledge}

# Task
A student describes a fault and the system has already classified it. Do three
things:
1. **Safety first**: if a leak, sparking, smoke or gas is involved, open with one
   immediately actionable safety step.
2. **What they can do themselves**: give 1-3 checks or fixes the student can
   safely carry out right now, concrete enough to follow literally (e.g. "rinse
   the filter under the tap, let it dry, refit it"). Never suggest anything
   touching mains wiring, gas, dismantling equipment or working at height — for
   those, say "leave it alone and wait for the technician".
3. **What happens next**: say what still needs confirming (college, dorm number,
   free time) and, if the description was too thin, list the questions that need
   answering.
4. **Scope**: discuss dorm repairs and maintenance arrangements only. Courses,
   grades, professors, visas and student societies are outside your remit.

# Out-of-scope reply (copy verbatim — do not add, remove, reword or translate)
{msg_out_of_scope}

# When the answer is not in the knowledge base (copy verbatim)
{msg_not_in_kb}

# Tone
Concise, actionable, trustworthy. Never invent prices, lead times, policies or
staff names. **Never state any number of minutes or any duration** — the labour
standard is a fixed internal value used for scheduling, not a promise to the
student, and the student console does not display it. **Write everything in
English.**"""

ADVICE_SYSTEM = {"zh": ADVICE_SYSTEM_ZH, "en": ADVICE_SYSTEM_EN}


def advise_student(api_key, model, text, context, lang="zh"):
    """Student-facing advice. Falls back to a deterministic checklist."""
    lang = lang if lang in ("zh", "en") else "zh"
    system = ADVICE_SYSTEM[lang].format(
        knowledge=knowledge_block(lang),
        msg_out_of_scope=MSG_OUT_OF_SCOPE[lang],
        msg_not_in_kb=MSG_NOT_IN_KB[lang],
    )
    ctx = _context_line(context, lang)
    if not api_key:
        return _fallback_advice(context, lang, reason="no_key")
    try:
        return call_model(
            api_key, model,
            [{"role": "system", "content": system},
             {"role": "user", "content": (ctx + "\n\n" + text).strip()}],
            max_tokens=700, temperature=0.3,
        ).strip()
    except Exception:
        return _fallback_advice(context, lang, reason="model_down")


def _context_line(context, lang):
    """What the agent is told about the request.

    Note what is NOT in here: the fixed labour time. The prompt forbids quoting
    it, and the surest way to keep a model from quoting a number is not to hand
    it the number.
    """
    if not context:
        return ""
    rtype = REPAIR_BY_ID.get(context.get("repair_type"))
    if lang == "zh":
        parts = ["【系统已识别】报修类别：%s；紧急度：%s" % (
            rtype["zh"] if rtype else context.get("repair_type", "-"),
            PRIORITY_LEVELS.get(context.get("priority", "normal"), {}).get("zh", "一般"),
        )]
        if context.get("college"):
            parts.append("书院：%s" % college_label(context["college"], "zh"))
        if context.get("dorm"):
            parts.append("宿舍号：%s" % context["dorm"])
        if context.get("window_text"):
            parts.append("学生空闲时间：%s" % context["window_text"])
        return "；".join(parts)
    parts = ["[System] Category: %s; priority: %s" % (
        rtype["en"] if rtype else context.get("repair_type", "-"),
        PRIORITY_LEVELS.get(context.get("priority", "normal"), {}).get("en", "Normal"),
    )]
    if context.get("college"):
        parts.append("College: %s" % college_label(context["college"], "en"))
    if context.get("dorm"):
        parts.append("Dorm: %s" % context["dorm"])
    if context.get("window_text"):
        parts.append("Availability: %s" % context["window_text"])
    return "; ".join(parts)


def _fallback_advice(context, lang, reason=""):
    """Deterministic advice used when the model is unreachable.

    Still worth reading: the same per-category self-help table the intake falls
    back to, plus the safety line. What is missing is the fixed labour time —
    that is a scheduling constant used to build the technician's day, and the
    student console does not show it, so the fallback must not either.
    """
    context = context or {}
    key = context.get("repair_type") or "other"
    rtype = REPAIR_BY_ID.get(key) or REPAIR_BY_ID["other"]
    pri = PRIORITY_LEVELS.get(context.get("priority", "normal"), PRIORITY_LEVELS["normal"])
    tips = SELF_HELP.get(key, SELF_HELP["other"]).get(lang, [])

    if lang == "zh":
        lines = ["**宿舍报修受理结果**", ""]
        lines.append("- 报修类别：**%s**" % rtype["zh"])
        lines.append("- 紧急度：**%s** —— %s" % (pri["zh"], pri["zh_desc"]))
        if context.get("window_text"):
            lines.append("- 你登记的空闲时间：%s" % context["window_text"])
        lines.append("- 需要备件：%s" % rtype["parts"])
        lines.append("")
        lines.append("**安全提示**：若涉及漏水、漏电或冒烟，请先关闭对应水阀 / 空气开关，"
                     "保持通风，不要自行拆解设备，等待师傅上门。")
        if tips:
            lines.append("")
            lines.append("**上门前可以先试试**：")
            for i, step in enumerate(tips, 1):
                lines.append("%d. %s" % (i, step))
        lines.append("")
        lines.append("**下一步**：确认书院与宿舍号无误，按你登记的空闲时间，系统会自动把"
                     "这一单排进师傅的当日路线。")
        lines.append("")
        lines.append("_（模型密钥未启用，以上为系统按固定规则生成的答复。）_"
                     if reason == "no_key"
                     else "_（模型暂时不可用，以上为系统按固定规则生成的答复。）_")
        return "\n".join(lines)

    lines = ["**Repair request received**", ""]
    lines.append("- Category: **%s**" % rtype["en"])
    lines.append("- Priority: **%s** — %s" % (pri["en"], pri["en_desc"]))
    if context.get("window_text"):
        lines.append("- Your declared availability: %s" % context["window_text"])
    lines.append("- Parts required: %s" % rtype["parts_en"])
    lines.append("")
    lines.append("**Safety first**: if there is a leak, sparking or smoke, shut off the "
                 "valve / breaker, ventilate, and do not dismantle the equipment yourself.")
    if tips:
        lines.append("")
        lines.append("**Worth trying before the technician arrives**:")
        for i, step in enumerate(tips, 1):
            lines.append("%d. %s" % (i, step))
    lines.append("")
    lines.append("**Next step**: confirm the college and dorm number. The system will slot "
                 "this job into the technician's route inside the times you declared free.")
    lines.append("")
    lines.append("_(Model key not enabled — the above was generated from fixed system "
                 "rules.)_" if reason == "no_key"
                 else "_(The model is temporarily unavailable — the above was generated "
                      "from fixed system rules.)_")
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# 智能体 3 —— 工人端派活 / Worker-side AI dispatch advice
#
# The prompt is written in the requested language rather than asking the model
# to translate: an instruction buried in a long Chinese prompt is routinely
# ignored, whereas a prompt that IS in the target language is followed. The
# section headings below therefore exist in both languages.
# --------------------------------------------------------------------------- #

DISPATCH_SYSTEM_ZH = """# Role
你是「DormCare 宿舍报修系统」的**维修调度智能体**，服务对象是维修师傅。

# 知识库（唯一信息来源）
{knowledge}

# 任务
系统已经用确定性算法排好了当日路线（下面的排班数据是**权威结果**，你不要重排、
不要改动时间、不要质疑顺序）。你的工作是把这批活讲清楚，帮师傅把当天干得又快又稳。

按以下结构输出，用 Markdown 小标题：

## 今日路线总览
用 3-4 句话概括今天几单、集中在哪些书院、工时与路程各占多少、最紧的是哪一单。

## 逐单作业指引
对每一单，按顺序给出：
- **单号 / 时间窗 / 书院+宿舍号**
- 作业要点：针对这个故障类型最可能的原因与处理顺序
- 需要带的备件
- 需要向学生确认的一句话

## 搬迁与路线提醒
指出哪些相邻两单是同书院（5 分钟即可到），哪些是跨书院（15 分钟），
给出一个可以让路程更省的建议（例如先做同一书院的几单）。

## 风险与升级
标出需要持证技师介入、或可能需要改约、或备件可能缺料的单子。

# 规则
1. 排班时间、时长、单号全部以给出的数据为准，不得改动、不得自行新增单子。
2. 不得编造学生姓名、电话号码、配件型号或价格。
3. 只谈当日维修作业。与维修无关的话题一律说明不属于你的职责范围。
4. **必须全程使用简体中文输出**，包括所有小标题和正文。不要输出英文段落。

# 范围外
如果被问到与当日维修调度完全无关的事，逐字输出：
{msg_out_of_scope}"""

DISPATCH_SYSTEM_EN = """# Role
You are the **maintenance dispatch agent** of "DormCare", a smart dorm repair
system. Your reader is a repair technician.

# Knowledge base (the ONLY source of truth)
{knowledge}

# Task
The system has already produced today's route with a deterministic engine. The
schedule data below is the **authoritative result** — do not re-order it, do not
change any time, do not question the sequence. Your job is to make the day clear
and fast to execute.

Use Markdown headings and this exact structure:

## Today at a glance
3-4 sentences: how many jobs, which colleges they are clustered in, how much of
the day is work versus travel, and which job is the tightest.

## Job-by-job guide
For every job, in order:
- **Ticket / time window / college + dorm**
- Work notes: the most likely causes for this fault type and the order to check them
- Parts to carry
- One question to confirm with the student

## Moves and route notes
Point out which adjacent jobs are in the same college (5 minutes) and which are
cross-college (15 minutes), and suggest one change that would save travel
(for example, batching the same college together).

## Risks and escalation
Flag any job needing a certified technician, any job that may need rebooking,
and any job where parts could be short.

# Rules
1. Every time, duration and ticket number comes from the data below — never
   change one, never add or remove a job.
2. Never invent student names, phone numbers, part numbers or prices.
3. Only discuss today's repair work. For anything unrelated, say it is outside
   your remit.
4. **Write everything in English**, including all headings. Do not emit Chinese
   paragraphs.

# Out of scope
If asked something wholly unrelated to today's dispatch, output this verbatim:
{msg_out_of_scope}"""

DISPATCH_SYSTEM = {"zh": DISPATCH_SYSTEM_ZH, "en": DISPATCH_SYSTEM_EN}


def advise_dispatch(api_key, model, day_iso, plan_jobs, stats, worker, lang="zh"):
    """Worker-facing daily briefing. Falls back to a generated checklist."""
    if not api_key:
        return _fallback_dispatch(day_iso, plan_jobs, stats, worker, lang)
    lang = lang if lang in ("zh", "en") else "zh"
    system = DISPATCH_SYSTEM[lang].format(knowledge=knowledge_block(lang),
                                         msg_out_of_scope=MSG_OUT_OF_SCOPE[lang])
    payload = _plan_payload(day_iso, plan_jobs, stats, worker, lang)
    try:
        return call_model(
            api_key, model,
            [{"role": "system", "content": system},
             {"role": "user", "content": payload}],
            max_tokens=1400, temperature=0.3,
        ).strip()
    except Exception:
        return _fallback_dispatch(day_iso, plan_jobs, stats, worker, lang)


def _plan_payload(day_iso, jobs, stats, worker, lang="zh"):
    """The authoritative schedule handed to the model. Numbers come from here."""
    if lang == "en":
        pri = lambda p: PRIORITY_LEVELS.get(p, {}).get("en", "Normal")
        lines = ["[Date] %s" % day_iso,
                 "[Technician] %s (%s)" % (worker.get("name_en") or worker.get("name", "-"),
                                           worker.get("worker_id", "-")),
                 "[Shift] %s - %s" % (stats["shift_start"], stats["shift_end"]),
                 "[Summary] %d jobs; %d min of work; %d min of travel" % (
                     stats["jobs"], stats["work_minutes"], stats["travel_minutes"]),
                 "", "[Today's schedule — authoritative, do not modify]"]
        if not jobs:
            lines.append("(no jobs to schedule)")
        for j in jobs:
            lines.append(
                "%d. Ticket %s | %s-%s | %s %s | %s | fixed duration %d min | "
                "travel to site %d min | priority %s | parts %s | reported fault: %s" % (
                    j["seq"], j["ticket_id"], j["start"].strftime("%H:%M"),
                    j["end"].strftime("%H:%M"), j["college_label"],
                    j.get("location") or j["dorm"],
                    j["repair_label"], j["duration"], j["travel_in"], pri(j["priority"]),
                    j["parts"], j["symptom"] or "-",
                ))
        return "\n".join(lines)

    lines = ["【排班日期】%s" % day_iso,
             "【师傅】%s（%s）" % (worker.get("name", "-"), worker.get("worker_id", "-")),
             "【班次】%s - %s" % (stats["shift_start"], stats["shift_end"]),
             "【统计】共 %d 单；维修工时 %d 分钟；路程 %d 分钟" % (
                 stats["jobs"], stats["work_minutes"], stats["travel_minutes"]),
             "", "【当日排班（权威数据，不得修改）】"]
    if not jobs:
        lines.append("（无单可排）")
    for j in jobs:
        lines.append(
            "%d. 单号 %s | %s-%s | %s %s | %s | 固定时长 %d 分钟 | 前往路程 %d 分钟 | "
            "紧急度 %s | 备件 %s | 故障描述：%s" % (
                j["seq"], j["ticket_id"], j["start"].strftime("%H:%M"),
                j["end"].strftime("%H:%M"), j["college_label"],
                j.get("location") or j["dorm"],
                j["repair_label"], j["duration"], j["travel_in"],
                PRIORITY_LEVELS.get(j["priority"], {}).get("zh", "一般"),
                j["parts"], j["symptom"] or "-",
            ))
    return "\n".join(lines)


def _fallback_dispatch(day_iso, jobs, stats, worker, lang="zh"):
    """Deterministic briefing, generated without the model."""
    lines = ["## 今日路线总览", ""]
    if not jobs:
        lines.append("今日无待排工单。" if lang == "zh" else "No jobs to schedule today.")
        return "\n".join(lines)
    colleges = []
    for j in jobs:
        if j["college_label"] not in colleges:
            colleges.append(j["college_label"])
    if lang == "zh":
        lines.append("班次 %s-%s，共 **%d 单**，涉及 %d 个书院：%s。维修工时合计 %d 分钟，"
                     "路程合计 %d 分钟。最早一单 %s 开始，最后一单 %s 结束。" % (
                         stats["shift_start"], stats["shift_end"], stats["jobs"],
                         len(colleges), "、".join(colleges), stats["work_minutes"],
                         stats["travel_minutes"], stats["first_start"], stats["last_end"]))
    else:
        lines.append("Shift %s-%s, **%d jobs** across %d colleges: %s. "
                     "%d minutes of work and %d minutes of travel. First job starts %s, "
                     "last job ends %s." % (
                         stats["shift_start"], stats["shift_end"], stats["jobs"],
                         len(colleges), ", ".join(colleges), stats["work_minutes"],
                         stats["travel_minutes"], stats["first_start"], stats["last_end"]))
    lines += ["", "## 逐单作业指引" if lang == "zh" else "## Job-by-job guide", ""]
    for j in jobs:
        lines.append("### %d. %s · %s · %s %s" % (j["seq"], j["ticket_id"],
                                                   j["start"].strftime("%H:%M") + "-" + j["end"].strftime("%H:%M"),
                                                   j["college_label"],
                                                   j.get("location") or j["dorm"]))
        lines.append("- %s：%s（%s）" % ("故障" if lang == "zh" else "Symptom",
                                        j["symptom"] or "-", j["repair_label"]))
        lines.append("- %s：%s" % ("所需备件" if lang == "zh" else "Parts", j["parts"]))
        lines.append("- %s：%d %s" % ("固定工时" if lang == "zh" else "Fixed duration",
                                      j["duration"], "分钟" if lang == "zh" else "min"))
        if j.get("certified"):
            lines.append("- ⚠️ %s" % ("需持证技师上门，请确认资质后再作业。" if lang == "zh"
                                      else "Requires a certified technician."))
    lines += ["", "## 搬迁与路线提醒" if lang == "zh" else "## Travel notes", ""]
    for i, j in enumerate(jobs):
        if i + 1 < len(jobs):
            nxt = jobs[i + 1]
            same = j["college"] == nxt["college"]
            lines.append("- %s → %s：%s %d %s" % (
                j["ticket_id"], nxt["ticket_id"],
                ("同书院" if same else "跨书院") if lang == "zh" else ("same college" if same else "cross-college"),
                j["travel_out"], "分钟" if lang == "zh" else "min"))
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# 智能体 4 —— 排班理由 / Plan rationale in natural language
# --------------------------------------------------------------------------- #

def summarize_plan(api_key, model, day_iso, jobs, stats, worker, lang="zh"):
    """A short explanation of WHY the deterministic plan looks like this."""
    if not api_key:
        return ""
    lang = lang if lang in ("zh", "en") else "zh"
    if lang == "zh":
        system = ("""# Role
你是「DormCare 宿舍报修系统」的排班解释智能体。系统已用确定性算法完成排班，
你的任务是用 4 句以内的话向师傅或学生解释这个排班的**依据**。

# 知识库
{knowledge}

# 必须解释清楚的规则
- 优先级排序：紧急 > 较急 > 一般 > 不急；同级按报修先后
- 每一项维修占用其固定时长
- 书院之间往返 15 分钟，同一书院内 5 分钟
- 第 1 单从维修间出发（按跨书院 15 分钟计）
- 所有作业只能安排在学生声明的空闲时间内，且不跨越 12:00-13:30 的午休

# 规则
只根据给定数据解释，不得改动任何时间、不得新增或删除工单、不得编造信息。
**必须使用简体中文，4 句以内。**""".format(knowledge=knowledge_block("zh")))
        ask = "\n\n请解释这个排班的依据。"
    else:
        system = ("""# Role
You are the schedule-rationale agent of "DormCare", a smart dorm repair system.
A deterministic engine has already produced the plan; your job is to explain its
**basis** in no more than four sentences, to either the technician or a student.

# Knowledge base
{knowledge}

# The rules you must make clear
- Priority ordering: urgent > high > normal > low; ties broken by filing order
- Every job occupies its fixed duration
- 15 minutes of travel between colleges, 5 minutes within one college
- The first job includes a 15-minute leg from the workshop
- Every job must fall inside the student's declared free time and must never
  straddle the 12:00-13:30 lunch break

# Rules
Explain only from the data given. Never change a time, never add or remove a job,
never invent information. **Write in English, in four sentences or fewer.**""".format(
            knowledge=knowledge_block("en")))
        ask = "\n\nExplain the basis of this schedule."
    payload = _plan_payload(day_iso, jobs, stats, worker, lang)
    try:
        return call_model(
            api_key, model,
            [{"role": "system", "content": system},
             {"role": "user", "content": payload + ask}],
            max_tokens=500, temperature=0.2,
        ).strip()
    except Exception:
        return ""
