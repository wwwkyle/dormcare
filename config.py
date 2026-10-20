#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
DormCare · 宿舍智能报修与反馈系统  —— 固定业务知识库 / Fixed business knowledge base
==================================================================================

This module is the SINGLE SOURCE OF TRUTH for everything the scheduling engine
and the AI agents are allowed to know:

    COLLEGES          the campus map        -> drives travel time
    REPAIR_TYPES      the repair catalogue  -> drives fixed duration
    TRAVEL_RULES      the travel constants  -> 15 min between colleges, 5 min inside
    RULES_TEXT        the RAG corpus        -> rendered into every system prompt

Nothing here is a guess made by a model. The model may only *explain* and
*classify*; all arithmetic is done by rules.py. That split is what makes the
schedule reproducible: same input, same plan, every time.
"""

# --------------------------------------------------------------------------- #
# 书院 / Colleges  —— 共 8 个书院 / eight colleges
#
# Hostel block information is recorded because it is what makes a dorm number
# meaningful to a technician: "Tower 3, East Block" tells them where to walk,
# whereas a bare room number does not. It also lets the AI agent answer
# "which block is this room in?" without inventing anything.
#
# `blocks` is the machine-readable list (used to populate the block picker);
# `blocks_zh` / `blocks_en` are the human-readable summary injected into the
# agent prompts.
# --------------------------------------------------------------------------- #

COLLEGES = [
    {
        "id": "shaw", "zh": "逸夫书院", "en": "Shaw College",
        "blocks": ["东座 1 栋", "东座 2 栋", "东座 3 栋", "西座 1 栋", "西座 2 栋", "西座 3 栋"],
        "blocks_zh": "分东座与西座，每座含 3 栋宿舍楼，合计 6 栋",
        "blocks_en": "West and East Blocks, each with three Towers (6 towers in total)",
    },
    {
        "id": "diligentia", "zh": "学勤书院", "en": "Diligentia College",
        "blocks": ["A 座", "B 座", "C 座"],
        "blocks_zh": "由 A、B、C 三座组成",
        "blocks_en": "Consists of Blocks A, B and C",
    },
    {
        "id": "muse", "zh": "思廷书院", "en": "Muse College",
        "blocks": ["宿舍区（栋号以现场标识为准）"],
        "blocks_zh": "具体宿舍栋号以现场标识为准",
        "blocks_en": "Specific hostel block information is not separately recorded",
    },
    {
        "id": "harmonia", "zh": "祥波书院", "en": "Harmonia College",
        "blocks": ["宿舍 1 栋", "宿舍 2 栋", "宿舍 3 栋", "宿舍 4 栋"],
        "blocks_zh": "由 4 栋学生宿舍组成，约 400 间宿舍",
        "blocks_en": "4 student hostels, approximately 400 dormitory rooms",
    },
    {
        "id": "ling", "zh": "道扬书院", "en": "Ling College",
        "blocks": ["A 座", "B 座", "C 座"],
        "blocks_zh": "由 A、B、C 三座组成",
        "blocks_en": "Consists of Blocks A, B and C",
    },
    {
        "id": "minerva", "zh": "厚含书院", "en": "Minerva College",
        "blocks": ["南区", "北区"],
        "blocks_zh": "分南区与北区，由三栋宿舍楼相互连通",
        "blocks_en": "Southern and northern zones interconnected through three dormitory buildings",
    },
    {
        "id": "duan", "zh": "永平书院", "en": "Duan Family College",
        "blocks": ["宿舍区（栋号以现场标识为准）"],
        "blocks_zh": "具体宿舍栋号以现场标识为准",
        "blocks_en": "Specific hostel block information is not separately recorded",
    },
    {
        "id": "eighth", "zh": "第八书院", "en": "Eighth College",
        "blocks": ["北区", "南区"],
        "blocks_zh": "由南北两个学生宿舍区组成",
        "blocks_en": "Two student hostels (North and South zones)",
    },
]

COLLEGE_BY_ID = {c["id"]: c for c in COLLEGES}

# --------------------------------------------------------------------------- #
# 固定工时 / Fixed repair durations
#
# "不同的类型的预计维修时间不一样，这个要固定" — every repair type has a LOCKED
# duration in minutes. The engine never estimates, never rounds and never
# asks the model to guess: it looks the number up in this table.
# --------------------------------------------------------------------------- #

REPAIR_TYPES = [
    {
        "id": "desk",
        "zh": "书桌", "en": "Desk",
        "minutes": 40,
        "zh_note": "抽屉卡滞、桌板松动、桌面破损更换",
        "en_note": "Stuck drawers, loose top, damaged surface replacement",
        "parts": "抽屉滑轨 / 螺丝 / 封边条",
        "parts_en": "Drawer runners / screws / edge banding",
    },
    {
        "id": "chair",
        "zh": "椅子", "en": "Chair",
        "minutes": 25,
        "zh_note": "气压杆失效、脚轮损坏、靠背断裂",
        "en_note": "Broken gas lift, damaged castors, cracked backrest",
        "parts": "气压杆 / 脚轮 / 靠背",
        "parts_en": "Gas lift / castors / backrest",
    },
    {
        "id": "bed",
        "zh": "床架 / 床板", "en": "Bed frame / slats",
        "minutes": 50,
        "zh_note": "床板断裂、护栏松脱、爬梯加固",
        "en_note": "Broken slats, loose guard rail, ladder reinforcement",
        "parts": "床板条 / 护栏 / 加固件",
        "parts_en": "Slats / guard rail / brackets",
    },
    {
        "id": "wardrobe",
        "zh": "衣柜", "en": "Wardrobe",
        "minutes": 35,
        "zh_note": "柜门脱落、合页损坏、挂杆断裂",
        "en_note": "Fallen door, broken hinges, snapped hanging rod",
        "parts": "合页 / 挂杆 / 磁吸",
        "parts_en": "Hinges / hanging rod / magnetic catch",
    },
    {
        "id": "ac",
        "zh": "空调", "en": "Air conditioner",
        "minutes": 60,
        "zh_note": "不制冷、漏水、异响、遥控失灵",
        "en_note": "Not cooling, leaking, noisy, remote not responding",
        "parts": "冷媒 / 排水管 / 滤网 / 电容",
        "parts_en": "Refrigerant / drain hose / filter / capacitor",
        "certified": True,          # 需持证上岗 / requires a certified technician
    },
    {
        "id": "ac_service",
        "zh": "空调深度清洗保养", "en": "AC deep clean & service",
        "minutes": 30,
        "zh_note": "滤网清洗、蒸发器除霉、风轮除尘",
        "en_note": "Filter wash, evaporator de-mould, blower dust removal",
        "parts": "清洁剂 / 滤网",
        "parts_en": "Cleaning agent / filter",
    },
    {
        "id": "light",
        "zh": "灯具 / 照明", "en": "Lighting",
        "minutes": 20,
        "zh_note": "灯管不亮、开关失灵、灯罩破损",
        "en_note": "Tube not lit, failed switch, broken shade",
        "parts": "LED 灯管 / 开关 / 灯罩",
        "parts_en": "LED tube / switch / shade",
    },
    {
        "id": "furniture",
        "zh": "家具五金", "en": "Furniture hardware",
        "minutes": 20,
        "zh_note": "抽屉导轨、柜锁、五金件松动更换",
        "en_note": "Drawer rails, cabinet locks, loose hardware replacement",
        "parts": "导轨 / 柜锁 / 五金件",
        "parts_en": "Rails / locks / hardware",
    },
    {
        "id": "plumbing",
        "zh": "水管 / 下水道", "en": "Plumbing / drainage",
        "minutes": 45,
        "zh_note": "漏水、下水道堵塞、水龙头滴水",
        "en_note": "Leaks, blocked drain, dripping tap",
        "parts": "密封圈 / 弯管 / 水龙头阀芯",
        "parts_en": "Seals / trap / tap cartridge",
    },
    {
        "id": "bathroom",
        "zh": "卫生间洁具", "en": "Bathroom fixtures",
        "minutes": 40,
        "zh_note": "花洒、马桶水箱、地漏、洗手台",
        "en_note": "Shower head, cistern, floor drain, wash basin",
        "parts": "花洒 / 水箱配件 / 地漏",
        "parts_en": "Shower head / cistern parts / floor drain",
    },
    {
        "id": "power",
        "zh": "电路 / 插座", "en": "Electrical / sockets",
        "minutes": 40,
        "zh_note": "插座无电、跳闸、开关面板损坏",
        "en_note": "Dead socket, tripping breaker, broken switch panel",
        "parts": "插座 / 空开 / 面板",
        "parts_en": "Socket / breaker / panel",
        "certified": True,          # 强电作业需持证电工
    },
    {
        "id": "network",
        "zh": "网络 / 网口", "en": "Network / LAN port",
        "minutes": 30,
        "zh_note": "网口不通、水晶头损坏、弱电箱整改",
        "en_note": "Dead LAN port, broken RJ45, weak-current box fix",
        "parts": "网线 / 水晶头 / 面板",
        "parts_en": "Cable / RJ45 plug / faceplate",
    },
    {
        "id": "appliance",
        "zh": "家电（热水器 / 洗衣机）", "en": "Appliance (heater / washer)",
        "minutes": 45,
        "zh_note": "热水器不加热、洗衣机不脱水、按键失灵",
        "en_note": "Heater not warming, washer not spinning, dead panel",
        "parts": "加热管 / 排水泵 / 控制板",
        "parts_en": "Heating element / drain pump / control board",
        "certified": True,
    },
    {
        "id": "door",
        "zh": "门 / 门锁 / 窗", "en": "Door, lock & window",
        "minutes": 30,
        "zh_note": "门锁卡死、闭门器失效、窗户关不严",
        "en_note": "Jammed lock, failed door closer, window not sealing",
        "parts": "锁芯 / 闭门器 / 密封条",
        "parts_en": "Cylinder / door closer / weatherstrip",
    },
    {
        "id": "floor",
        "zh": "地板 / 墙砖", "en": "Floor & wall tiles",
        "minutes": 45,
        "zh_note": "地板起翘、瓷砖空鼓脱落、踢脚线松脱",
        "en_note": "Lifting floor, hollow fallen tiles, loose skirting",
        "parts": "瓷砖 / 收边条 / 胶",
        "parts_en": "Tiles / trim / adhesive",
    },
    {
        "id": "curtain",
        "zh": "窗帘 / 窗帘轨道", "en": "Curtains & track",
        "minutes": 25,
        "zh_note": "轨道脱落、拉绳卡住、遮光帘破损",
        "en_note": "Fallen track, jammed cord, torn blackout curtain",
        "parts": "轨道 / 滑轮 / 挂钩",
        "parts_en": "Track / runners / hooks",
    },
    {
        "id": "other",
        "zh": "其他报修", "en": "Other",
        "minutes": 60,
        "zh_note": "未列入以上类别的报修，按 60 分钟预排",
        "en_note": "Anything not listed above; pre-scheduled at 60 minutes",
        "parts": "视现场情况",
        "parts_en": "Determined on site",
    },
]

REPAIR_BY_ID = {r["id"]: r for r in REPAIR_TYPES}

# --------------------------------------------------------------------------- #
# 路程规则 / Travel rules  —— 与需求逐字对应
# --------------------------------------------------------------------------- #

TRAVEL_RULES = {
    "cross_college_minutes": 15,   # 书院之间往返 / between colleges
    "same_college_minutes": 5,     # 书院内 / inside the same college
    "workday_start": "08:30",      # 工人上班时间 / shift start
    "workday_end": "18:00",        # 工人下班时间 / shift end
    "lunch_start": "12:00",        # 午休开始 / lunch break start
    "lunch_end": "13:30",          # 午休结束 / lunch break end
    "scheduling_days": 5,          # 只给未来 5 个工作日排班
}

# --------------------------------------------------------------------------- #
# RAG 语料 / Retrieval corpus
#
# This text is assembled into the system prompt of BOTH agents and into the
# LLM intent parser. It is the only knowledge the model has. Everything else
# the model might "know" about dorm repairs is explicitly out of scope.
# --------------------------------------------------------------------------- #

def _fmt_minutes(m):
    if m < 60:
        return "%d 分钟" % m
    if m % 60 == 0:
        return "%d 小时" % (m // 60)
    return "%d 小时 %d 分钟" % (m // 60, m % 60)


def build_repair_table(zh=True):
    """Render the repair catalogue as a Markdown table for the prompt."""
    if zh:
        head = "| 类别ID | 报修类型 | 固定维修时长 | 说明 | 需要持证技师 |"
    else:
        head = "| Type ID | Repair type | Fixed duration | Description | Certified tech |"
    lines = [head, "| --- | --- | --- | --- | --- |"]
    for r in REPAIR_TYPES:
        lines.append("| `%s` | %s | %s | %s | %s |" % (
            r["id"],
            r["zh"] if zh else r["en"],
            _fmt_minutes(r["minutes"]),
            r["zh_note"] if zh else r["en_note"],
            ("是" if zh else "yes") if r.get("certified") else ("否" if zh else "no"),
        ))
    return "\n".join(lines)


def build_college_table(zh=True):
    if zh:
        head = "| 书院ID | 书院名称 | 宿舍楼栋信息 |"
    else:
        head = "| College ID | College name | Hostel / block information |"
    lines = [head, "| --- | --- | --- |"]
    for c in COLLEGES:
        lines.append("| `%s` | %s | %s |" % (
            c["id"],
            c["zh"] if zh else c["en"],
            c["blocks_zh"] if zh else c["blocks_en"],
        ))
    return "\n".join(lines)


def repair_minutes(repair_id):
    """Fixed duration for a repair type. Unknown types fall back to 'other'."""
    r = REPAIR_BY_ID.get(repair_id) or REPAIR_BY_ID["other"]
    return r["minutes"]


def repair_label(repair_id, lang="zh"):
    r = REPAIR_BY_ID.get(repair_id)
    if not r:
        return repair_id
    return r["zh"] if lang == "zh" else r["en"]


def college_label(college_id, lang="zh"):
    c = COLLEGE_BY_ID.get(college_id)
    if not c:
        return college_id
    return c["zh"] if lang == "zh" else c["en"]


def college_blocks(college_id):
    """Block names recorded for a college (may be a single catch-all entry)."""
    c = COLLEGE_BY_ID.get(college_id)
    return list(c["blocks"]) if c else []


def college_blocks_text(college_id, lang="zh"):
    c = COLLEGE_BY_ID.get(college_id)
    if not c:
        return ""
    return c["blocks_zh"] if lang == "zh" else c["blocks_en"]


#: Two colleges record their towers only as this catch-all because the numbers
#: are printed on the door itself. A student there has nothing to pick, so the
#: tower is stored as an empty string and the dorm number carries the address.
PLACEHOLDER_BLOCK = "以现场标识为准"


def college_real_blocks(college_id):
    """Tower names a student can actually choose — the catch-all removed."""
    return [b for b in college_blocks(college_id) if PLACEHOLDER_BLOCK not in b]


def normalize_block(college_id, raw):
    """Canonical tower for `college_id`, or "" when it cannot be trusted.

    A supplied tower must appear verbatim in that college's recorded list.
    Anything else — a typo, another college's tower, a stale value — is
    dropped rather than stored, so `block` can never contradict `college`.
    """
    raw = (raw or "").strip()[:60]
    real = college_real_blocks(college_id)
    if not real:
        return ""
    return raw if raw in real else ""
