"""LLM-based voice recommendation from user's persona description."""
import json
import re

from app.core.model_adapter import call_once

# 精选中文音色，涵盖主要风格
VOICES: list[dict] = [
    {"id": "male-qn-qingse",                          "name": "青涩青年",  "desc": "清新青涩"},
    {"id": "male-qn-jingying",                         "name": "精英青年",  "desc": "干练理性"},
    {"id": "male-qn-badao",                            "name": "霸道青年",  "desc": "强势霸气"},
    {"id": "male-qn-daxuesheng",                       "name": "青年大学生","desc": "阳光活力"},
    {"id": "female-shaonv",                            "name": "少女",      "desc": "清纯甜美"},
    {"id": "female-yujie",                             "name": "御姐",      "desc": "成熟气场"},
    {"id": "female-chengshu",                          "name": "成熟女性",  "desc": "稳重知性"},
    {"id": "female-tianmei",                           "name": "甜美女性",  "desc": "温柔亲切"},
    {"id": "danya_xuejie",                             "name": "淡雅学姐",  "desc": "淡雅冷静"},
    {"id": "lengdan_xiongzhang",                       "name": "冷淡学长",  "desc": "低调冷淡"},
    {"id": "junlang_nanyou",                           "name": "俊朗男友",  "desc": "温柔体贴"},
    {"id": "wumei_yujie",                              "name": "妩媚御姐",  "desc": "妩媚成熟"},
    {"id": "Chinese (Mandarin)_Reliable_Executive",    "name": "沉稳高管",  "desc": "专业权威"},
    {"id": "Chinese (Mandarin)_Wise_Women",            "name": "阅历姐姐",  "desc": "睿智亲和"},
    {"id": "Chinese (Mandarin)_Gentleman",             "name": "温润男声",  "desc": "儒雅有礼"},
    {"id": "Chinese (Mandarin)_Gentle_Youth",          "name": "温润青年",  "desc": "温柔阳光"},
    {"id": "Chinese (Mandarin)_Radio_Host",            "name": "电台男主播","desc": "磁性低沉"},
    {"id": "Chinese (Mandarin)_Warm_Girl",             "name": "温暖少女",  "desc": "温暖活泼"},
    {"id": "Chinese (Mandarin)_Sincere_Adult",         "name": "真诚青年",  "desc": "真诚直接"},
    {"id": "Robot_Armor",                              "name": "机械战甲",  "desc": "科技冷酷"},
]

_FALLBACK = [
    {"id": "danya_xuejie",                          "name": "淡雅学姐", "reason": "温柔冷静，适合多种场景"},
    {"id": "male-qn-jingying",                      "name": "精英青年", "reason": "干练理性，专业感强"},
    {"id": "Chinese (Mandarin)_Wise_Women",         "name": "阅历姐姐", "reason": "睿智亲和，娓娓道来"},
]

_SYSTEM = "你是音色推荐助手，只输出 JSON，不输出其他内容。"
_PROMPT = """\
用户希望的助手特征：{description}

可选音色（voice_id | 名称 | 风格）：
{voices}

从中选出最匹配的3个，按匹配度排序，JSON格式：
{{"recommendations":[{{"id":"...","name":"...","reason":"一句话理由"}}]}}"""


async def recommend_voices(description: str) -> list[dict]:
    voices_text = "\n".join(f"{v['id']} | {v['name']} | {v['desc']}" for v in VOICES)
    prompt = _PROMPT.format(description=description, voices=voices_text)
    try:
        raw = await call_once(
            messages=[{"role": "user", "content": prompt}],
            system=_SYSTEM,
            max_tokens=512,
        )
        raw = re.sub(r"^```(?:json)?\s*", "", raw.strip())
        raw = re.sub(r"\s*```$", "", raw)
        data = json.loads(raw)
        recs = data.get("recommendations", [])[:3]
        if recs:
            return recs
    except Exception:  # noqa: BLE001
        pass
    return _FALLBACK


def get_extra_voices(exclude_ids: list[str]) -> list[dict]:
    """Return remaining voices not in the top recommendations."""
    exclude = set(exclude_ids)
    return [v for v in VOICES if v["id"] not in exclude]


_SOUL_SYSTEM = "你是信息提取助手，只输出 JSON，不输出其他内容。"
_SOUL_PROMPT = """\
用户的初始化回答：{description}

从中提取：
1. 用户自己的称呼（用户叫什么，若未提及用"用户"）
2. 用户希望叫助手的名字（若未提及用"助手"）
3. 助手个性特征（2-4条，每条一行，以"- "开头）

JSON格式：{{"user_name":"...","name":"...","personality":"- 特征1\\n- 特征2"}}"""


async def extract_soul_draft(description: str) -> dict[str, str]:
    """Extract user name, assistant name and personality from onboarding reply."""
    try:
        raw = await call_once(
            messages=[{"role": "user", "content": _SOUL_PROMPT.format(description=description)}],
            system=_SOUL_SYSTEM,
            max_tokens=256,
        )
        raw = re.sub(r"^```(?:json)?\s*", "", raw.strip())
        raw = re.sub(r"\s*```$", "", raw)
        data = json.loads(raw)
        return {
            "user_name": data.get("user_name", "用户") or "用户",
            "name": data.get("name", "助手") or "助手",
            "personality": data.get("personality", "- 简洁直接\n- 专业友好"),
        }
    except Exception:  # noqa: BLE001
        return {"user_name": "用户", "name": "助手", "personality": "- 简洁直接\n- 专业友好"}
