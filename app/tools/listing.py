"""03 Listing 创意：标题合规、规格溯源、关键词分层的确定性校验。

为什么这些判断必须放在 Python 里：
- 标题字符数、前 30 字符权重、全大写比例、符号数量都是平台硬规则，
  让模型"目测"一定会在边界上翻车；
- 规格能否逐条溯源到事实表，是 listing 可信度的底线，必须结构化比对而不是"看起来对"；
- 关键词分层决定广告词包，去重与分词口径必须可复现、可单测。
"""

from __future__ import annotations

import math
import re
import unicodedata
from collections import Counter
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.tools.base import ToolError, tool
from app.tools.platform_data import BANNED_TERMS, EXAGGERATION_TERMS, PLATFORM_RULES

# 标题中按「符号」计数的显式字符（emoji 另外按 Unicode 区间判断）
_SYMBOL_CHARS = frozenset("#!@$%&*~")

# 词堆砌检测：按非字母切分，保留葡语重音字母（aço / elétrico）
_WORD_RE = re.compile(r"[^\W\d_]+", re.UNICODE)

# 关键词分词：允许数字，如 "patinete 500w" / "48v"
_TOKEN_RE = re.compile(r"[^\W_]+", re.UNICODE)

# 数值型规格：数字 + 可选单位（单位不参与比较，只做剥离）
_NUMBER_RE = re.compile(r"([+-]?\d+(?:[.,]\d+)?)([a-z%°/]*)")


def _fold(text: str) -> str:
    """小写 + 去重音：让 "Grátis"/"gratis"、"AÇÃO"/"acao" 落到同一口径。"""
    decomposed = unicodedata.normalize("NFKD", (text or "").casefold())
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch))


# 否定意图词：出现即判 negative，不进 primary / long_tail
NEGATIVE_TERMS: tuple[str, ...] = (
    "usado",
    "quebrado",
    "defeito",
    "reclamação",
    "barato demais",
    "grátis",
    "segunda mão",
    "usada",
)

# 用词边界匹配，避免 "usado" 误伤 "usador" 这类长词
_NEGATIVE_PATTERN = re.compile(r"(?<!\w)(?:" + "|".join(re.escape(_fold(term)) for term in NEGATIVE_TERMS) + r")(?!\w)")


def _is_symbol_char(ch: str) -> bool:
    """是否为符号 / emoji（标题符号滥用检测）。"""
    if ch in _SYMBOL_CHARS:
        return True
    cp = ord(ch)
    if cp in (0x200D, 0xFE0F, 0x20E3):  # ZWJ / 变体选择符 / 组合键帽
        return True
    if 0x1F000 <= cp <= 0x1FAFF:  # emoji 主区间
        return True
    if 0x2600 <= cp <= 0x27BF:  # Misc Symbols + Dingbats
        return True
    return unicodedata.category(ch) == "So"


def _resolve_platform(platform: str) -> tuple[str, dict[str, Any], list[str]]:
    """平台规则解析：未知平台回退 shopee，并把回退原因写进 notes。"""
    key = (platform or "shopee").strip().lower()
    if key in PLATFORM_RULES:
        return key, PLATFORM_RULES[key], []
    return "shopee", PLATFORM_RULES["shopee"], [f"未知平台 {platform!r}，已回退 shopee 规则"]


class TitleLintArgs(BaseModel):
    """title_lint 入参。"""

    model_config = ConfigDict(extra="forbid")

    text: str = Field(description="待校验标题（原样传入，工具不做预清洗）")
    platform: str = Field(default="shopee", description="shopee|mercadolivre|amazon|tiktok|shopify")
    keywords: list[str] = Field(default_factory=list, description="目标关键词，用于前 30 字符命中校验")
    language: str = Field(default="pt-BR", description="标题语言，仅记录在结果里")


@tool(
    "title_lint",
    "校验标题字符上限、前 30 字符关键词、词堆砌、平台禁用词、夸大词、全大写与符号滥用，返回逐项 checks 与总 passed。",
    TitleLintArgs,
    tags=("03",),
)
def title_lint(a: TitleLintArgs) -> dict[str, Any]:
    platform, rules, notes = _resolve_platform(a.platform)
    text = a.text or ""
    chars = len(text)
    limit = int(rules["title_limit"])
    head_weight_chars = int(rules["head_weight_chars"])
    head30 = text[:30]
    folded_text = _fold(text)
    folded_head = _fold(head30)

    keywords = [kw.strip() for kw in a.keywords if kw and kw.strip()]
    matched_keyword = next((kw for kw in keywords if _fold(kw) in folded_head), None)
    head_has_keyword = matched_keyword is not None
    positions = [folded_text.find(_fold(kw)) for kw in keywords]
    positions = [pos for pos in positions if pos >= 0]
    primary_position = min(positions) if positions else -1

    # 词堆砌：同一词（大小写/重音不敏感）出现 3 次及以上
    token_counts = Counter(_WORD_RE.findall(text.casefold()))
    repeated_pairs = sorted(
        ((token, count) for token, count in token_counts.items() if count > 2),
        key=lambda item: (-item[1], item[0]),
    )
    repeated_tokens = [token for token, _ in repeated_pairs]

    banned_hits = [term for term in BANNED_TERMS.get(platform, []) if _fold(term) in folded_text]
    exaggeration_hits = [term for term in EXAGGERATION_TERMS if _fold(term) in folded_text]

    letters = [ch for ch in text if ch.isalpha()]
    raw_uppercase_ratio = sum(1 for ch in letters if ch.isupper()) / len(letters) if letters else 0.0
    uppercase_ratio = round(raw_uppercase_ratio, 4)
    symbol_count = sum(1 for ch in text if _is_symbol_char(ch))

    within_limit = chars <= limit
    head_ok = head_has_keyword or not keywords
    stuffing_ok = not repeated_tokens
    uppercase_ok = raw_uppercase_ratio <= 0.5
    symbols_ok = symbol_count <= 4

    if not keywords:
        head_detail = "未提供关键词，跳过前段关键词校验"
    elif matched_keyword is not None:
        head_detail = f"前 30 字符命中关键词 {matched_keyword!r}（位置 {primary_position}）"
    else:
        head_detail = f"前 30 字符未命中任何关键词：{', '.join(keywords)}"

    checks: list[dict[str, Any]] = [
        {
            "id": "length",
            "name": "字符上限",
            "passed": within_limit,
            "detail": f"{chars}/{limit} 字符（上限 {limit}）" + ("" if within_limit else "，已超限"),
        },
        {
            "id": "head_keyword",
            "name": "前段关键词",
            "passed": head_ok,
            "detail": head_detail,
        },
        {
            "id": "stuffing",
            "name": "词堆砌",
            "passed": stuffing_ok,
            "detail": (
                "无同一词出现 3 次及以上"
                if stuffing_ok
                else "重复词：" + ", ".join(f"{token}×{count}" for token, count in repeated_pairs)
            ),
        },
        {
            "id": "banned",
            "name": "平台禁用词",
            "passed": not banned_hits,
            "detail": "未命中平台禁用词" if not banned_hits else "命中禁用词：" + ", ".join(banned_hits),
        },
        {
            "id": "exaggeration",
            "name": "夸大词",
            "passed": not exaggeration_hits,
            "detail": "未命中夸大词" if not exaggeration_hits else "命中夸大词：" + ", ".join(exaggeration_hits),
        },
        {
            "id": "uppercase",
            "name": "全大写比例",
            "passed": uppercase_ok,
            "detail": f"大写字母占比 {uppercase_ratio:.1%}（阈值 <=50%）",
        },
        {
            "id": "symbols",
            "name": "符号数量",
            "passed": symbols_ok,
            "detail": f"符号/emoji 数 {symbol_count}（阈值 <=4）",
        },
    ]

    passed = all(check["passed"] for check in checks) and not banned_hits and within_limit

    return {
        "platform": platform,
        "language": a.language,
        "chars": chars,
        "limit": limit,
        "within_limit": within_limit,
        "head_weight_chars": head_weight_chars,
        "head30": head30,
        "head_has_keyword": head_has_keyword,
        "primary_position": primary_position,
        "repeated_tokens": repeated_tokens,
        "banned_hits": banned_hits,
        "exaggeration_hits": exaggeration_hits,
        "uppercase_ratio": uppercase_ratio,
        "symbol_count": symbol_count,
        "checks": checks,
        "passed": passed,
        "notes": notes,
    }


class SpecConsistencyArgs(BaseModel):
    """spec_consistency 入参。"""

    model_config = ConfigDict(extra="forbid")

    specs: list[dict[str, Any]] = Field(
        default_factory=list,
        description='规格列表，每项 {"name": str, "value": str, "source": "fact"|"derived"（可选）}',
    )
    facts: dict[str, str] = Field(description="商品事实表：规格名 -> 可溯源取值")


def _normalize_key(name: Any) -> str:
    """规格名归一：去重音、小写、去首尾空白。"""
    return _fold(str(name)).strip()


def _canonical(value: Any) -> tuple[str, float | str]:
    """规格值归一：小写、去重音、去空白、逗号小数点统一、剥离单位。

    例："18,5 kg" -> ("num", 18.5)；"Aço Inox" -> ("str", "aco inox")。
    """
    raw = "" if value is None else str(value)
    folded = _fold(unicodedata.normalize("NFKC", raw))
    compact = re.sub(r"\s+", "", folded)
    match = _NUMBER_RE.fullmatch(compact)
    if match:
        return ("num", float(match.group(1).replace(",", ".")))
    return ("str", re.sub(r"\s+", " ", folded).strip(" .,;:"))


def _same_value(left: str, right: str) -> bool:
    """两个规格值是否等价（数值按数值比较，其余按归一化字符串比较）。"""
    left_kind, left_value = _canonical(left)
    right_kind, right_value = _canonical(right)
    if left_kind != right_kind:
        return False
    if left_kind == "num":
        return math.isclose(float(left_value), float(right_value), rel_tol=1e-9, abs_tol=1e-9)
    return str(left_value) == str(right_value)


@tool(
    "spec_consistency",
    "逐条比对 listing 规格与商品事实表：归一化大小写/空白/单位/小数符号，"
    "输出 mismatch / untraceable / unverified 与溯源率。",
    SpecConsistencyArgs,
    tags=("03",),
)
def spec_consistency(a: SpecConsistencyArgs) -> dict[str, Any]:
    if not a.specs:
        raise ToolError("spec_consistency", "specs 不能为空：至少提供 1 条待校验规格")

    facts = {_normalize_key(key): value for key, value in a.facts.items()}
    issues: list[dict[str, Any]] = []
    traced = 0

    for idx, spec in enumerate(a.specs):
        if not isinstance(spec, dict):
            raise ToolError("spec_consistency", f"specs[{idx}] 必须是对象")
        if spec.get("name") is None or str(spec.get("name", "")).strip() == "":
            raise ToolError("spec_consistency", f"specs[{idx}] 缺少 name")
        if "value" not in spec:
            raise ToolError("spec_consistency", f"specs[{idx}]（{spec['name']}）缺少 value")

        name = str(spec["name"])
        spec_value = "" if spec["value"] is None else str(spec["value"])
        source = spec.get("source")
        source_key = str(source).strip().lower() if source is not None else ""
        key = _normalize_key(name)

        if key in facts:
            traced += 1
            fact_value = "" if facts[key] is None else str(facts[key])
            if not _same_value(spec_value, fact_value):
                issues.append({"name": name, "spec_value": spec_value, "fact_value": fact_value, "type": "mismatch"})
            continue

        if source_key == "derived":
            # derived 是由事实表计算得出的派生值，不要求命中事实表
            continue

        issue_type = "untraceable" if source_key == "fact" else "unverified"
        issues.append({"name": name, "spec_value": spec_value, "fact_value": None, "type": issue_type})

    checked = len(a.specs)
    passed = not any(issue["type"] in {"mismatch", "untraceable"} for issue in issues)
    return {
        "checked": checked,
        "issues": issues,
        "passed": passed,
        "traceability_rate": round(traced / checked, 3),
    }


class KeywordClassifyArgs(BaseModel):
    """keyword_classify 入参。"""

    model_config = ConfigDict(extra="forbid")

    keywords: list[str] = Field(default_factory=list, description="待分层关键词（保持传入顺序）")
    top_n: int = Field(default=5, ge=1, le=100, description="primary 最多返回词数（推荐投放的头部词）")


@tool(
    "keyword_classify",
    "关键词去重并按词数分层为 primary / long_tail / negative（否定意图词单独隔离），返回各层计数与去重后总数。",
    KeywordClassifyArgs,
    tags=("03",),
)
def keyword_classify(a: KeywordClassifyArgs) -> dict[str, Any]:
    seen: set[str] = set()
    primary_pool: list[str] = []
    long_tail: list[str] = []
    negative: list[str] = []

    for raw in a.keywords:
        keyword = " ".join(str(raw or "").split())
        if not keyword:
            continue
        key = _fold(keyword)
        if key in seen:
            continue
        seen.add(key)
        if _NEGATIVE_PATTERN.search(key):
            negative.append(keyword)
        elif len(_TOKEN_RE.findall(key)) <= 2:
            primary_pool.append(keyword)
        else:
            long_tail.append(keyword)

    primary = primary_pool[: a.top_n]
    return {
        "primary": primary,
        "long_tail": long_tail,
        "negative": negative,
        "counts": {
            "primary": len(primary),
            "long_tail": len(long_tail),
            "negative": len(negative),
        },
        "total": len(primary_pool) + len(long_tail) + len(negative),
        "truncated": len(primary_pool) - len(primary),
    }
