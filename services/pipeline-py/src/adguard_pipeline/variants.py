"""Variant generators for the M2 evaluation set (7 attack families).

The generators are intentionally *independent* from the matcher's dictionaries:
the dataset plants a known transformation, the matcher must recover it. Keeping
generation and recognition separate is what makes the hit-rate metric meaningful.
"""

from __future__ import annotations

import random
from dataclasses import dataclass

# 拆字 map: canonical char -> plausible component spelling (kept in step with
# data/normalization/char_map.json component_sequences).
COMPONENT_MAP: dict[str, str] = {
    "最": "曰取",
    "佳": "亻圭",
    "级": "纟及",
    "国": "囗玉",
    "顶": "丁页",
    "唯": "隹一",
    "冠": "冖元",
    "尊": "至寸",
    "美": "羙",
}

# 形近/异体字 map (not necessarily the same as the normalizer map: this is the
# attack generator's source of truth, and the matcher must still handle it).
VARIANT_CHAR_MAP: dict[str, str] = {
    "最": "冣",
    "美": "羙",
    "级": "級",
    "国": "國",
    "顶": "頂",
    "极": "極",
    "绝": "絕",
    "无": "無",
    "敌": "敵",
    "独": "獨",
    "领": "領",
    "销": "銷",
}

EMOJIS = ["😀", "😎", "🔥", "💰", "🎉", "⚡", "✨", "👍", "🛒", "💥"]
ZERO_WIDTHS = ["\u200b", "\u200c", "\u200d", "\ufeff", "\u2060"]

# Same-syllable characters used for 谐音 samples that are *not* in the dictionary,
# to measure the honest out-of-scope rate (unknown homophones).
UNKNOWN_HOMOPHONE_CHARS: dict[str, list[str]] = {
    "最": ["檇", "蕞", "晬"],
    "佳": ["枷", "珈", "痂"],
    "级": ["亟", "笈", "汲"],
    "国": ["帼", "虢", "腘"],
    "第": ["棣", "睇", "缔"],
    "一": ["伊", "漪", "猗"],
}

# Semantic paraphrases deliberately OUTSIDE the lexical dictionary: these are the
# M2 known limitation, expected to be handled by the M4 semantic model.
SEMANTIC_PARAPHRASES = [
    "无人能及",
    "天下无双",
    "冠绝群伦",
    "无可超越",
    "绝顶",
    "登峰",
    "睥睨群雄",
    "独领风骚",
    "傲视同侪",
    "无双",
]

TEMPLATES = [
    "本店商品{term}，欢迎选购。",
    "【限时促销】全场{term}，仅此一天！",
    "为什么说这款产品{term}？看完你就懂了。",
    "用户评价：这是{term}的选择。",
    "广告语：{term}！",
    "我们承诺为您提供{term}的服务。",
    "官方宣布：本产品荣获{term}称号。",
    "选购指南 | {term}的品质之选。",
]

NEGATIVE_TEMPLATES = [
    "本店商品质量可靠，支持七天无理由退换。",
    "【限时促销】全场满减，仅此一天。",
    "为什么说这款产品值得买？看完你就懂了。",
    "用户评价：包装完好，物流很快。",
    "广告语：用心做好每一件商品。",
    "我们承诺为您提供贴心周到的售后服务。",
    "官方宣布：新品将于下月正式发布。",
    "选购指南 | 如何挑选适合自己的商品。",
]


@dataclass(frozen=True)
class PlantedSample:
    text: str
    span: tuple[int, int]


def _plant(template: str, term: str) -> PlantedSample:
    prefix = template.split("{term}")[0]
    text = template.format(term=term)
    start = len(prefix)
    return PlantedSample(text=text, span=(start, start + len(term)))


def pinyin_variants(term: str, rng: random.Random) -> list[str]:
    from pypinyin import Style, lazy_pinyin

    syllables = lazy_pinyin(term, style=Style.NORMAL)
    tone3 = lazy_pinyin(term, style=Style.TONE3, neutral_tone_with_five=True)
    forms = [
        " ".join(syllables),
        "".join(syllables),
        " ".join(tone3),
        "".join(tone3),
    ]
    if len(syllables) >= 2:
        forms.append(" ".join(syllables[:-1]) + " " + syllables[-1])
        forms.append(syllables[0] + "-" + "".join(syllables[1:]))
    return sorted(set(forms))


def homophone_forms(term: str, homophones: tuple[str, ...]) -> list[str]:
    forms: list[str] = list(homophones)
    for index, char in enumerate(term):
        for replacement in UNKNOWN_HOMOPHONE_CHARS.get(char, ()):
            forms.append(term[:index] + replacement + term[index + 1 :])
    return sorted(set(forms))


def split_char_forms(term: str) -> list[str]:
    forms: list[str] = []
    for index, char in enumerate(term):
        component = COMPONENT_MAP.get(char)
        if component:
            forms.append(term[:index] + component + term[index + 1 :])
    return forms


def variant_char_forms(term: str) -> list[str]:
    forms: list[str] = []
    for index, char in enumerate(term):
        replacement = VARIANT_CHAR_MAP.get(char)
        if replacement:
            forms.append(term[:index] + replacement + term[index + 1 :])
    # Traditional spellings via OpenCC (whole-term conversion) are another family.
    try:
        from opencc import OpenCC

        converted = OpenCC("s2t").convert(term)
        if converted != term:
            forms.append(converted)
    except ImportError:  # pragma: no cover
        pass
    return sorted(set(forms))


def emoji_forms(term: str, rng: random.Random) -> list[str]:
    if len(term) < 2:
        return []
    first, last = rng.sample(EMOJIS, 2)
    return [first + "".join(term) if len(term) == 1 else term[0] + first + term[1:],
            term[:-1] + last + term[-1]]


def zero_width_forms(term: str, rng: random.Random) -> list[str]:
    if len(term) < 2:
        return []
    forms = []
    for _ in range(2):
        index = rng.randrange(1, len(term))
        zw = rng.choice(ZERO_WIDTHS)
        forms.append(term[:index] + zw + term[index:])
    return forms


def fullwidth_forms(term: str) -> list[str]:
    """Full-width forms (NFKC folds them; digits/ascii only in practice)."""
    forms = []
    full = "".join(chr(ord(ch) + 0xFEE0) if "!" <= ch <= "~" else ch for ch in term)
    if full != term:
        forms.append(full)
    return forms


def unknown_semantic_forms(rng: random.Random, count: int) -> list[str]:
    return rng.sample(SEMANTIC_PARAPHRASES, min(count, len(SEMANTIC_PARAPHRASES)))
