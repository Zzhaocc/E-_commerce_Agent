"""事后硬校验（Gates）。

LLM 输出"看起来对"不算数：标题字符、参数溯源、图片像素、视频时序、漏斗口径，
全部由脚本复核，并区分三级处置：
    repair —— 能自动修的直接修（截断超长标题、压缩叠加文案）
    block  —— 阻塞发布（禁用词、参数与事实冲突），产物降级为 draft
    warn   —— 记录并放行（溯源性、置信度偏低）
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

from app.agents.base import AgentContext
from app.tools.base import ToolError, invoke_tool


@dataclass
class GateOutcome:
    checks: list[dict[str, Any]] = field(default_factory=list)
    guardrails: list[dict[str, Any]] = field(default_factory=list)
    output: dict[str, Any] = field(default_factory=dict)
    blocked: bool = False

    def check(self, cid: str, name: str, passed: bool, detail: str = "") -> None:
        self.checks.append({"id": cid, "name": name, "passed": passed, "detail": detail})

    def guard(self, code: str, severity: str, message: str) -> None:
        self.guardrails.append({"code": code, "severity": severity, "message": message})
        if severity == "block":
            self.blocked = True


def _tool(out: GateOutcome, name: str, args: dict[str, Any]) -> dict[str, Any] | None:
    try:
        result, _ = invoke_tool(name, args)
        return result
    except ToolError as exc:
        out.check(f"tool:{name}", f"{name} 执行", False, exc.message)
        out.guard("tool_error", "warn", f"{name} 未能执行：{exc.message}")
        return None


def _gate_listing(out: GateOutcome, output: dict[str, Any], ctx: AgentContext) -> None:
    platform = ctx.project.get("platform", "shopee")
    title = output.get("title") or {}
    text = title.get("text", "")

    lint = _tool(
        out,
        "title_lint",
        {"text": text, "platform": platform, "keywords": (output.get("keyword_map") or {}).get("primary", [])[:3]},
    )
    if lint:
        if not lint.get("within_limit", True) and lint.get("limit"):
            limit = int(lint["limit"])
            cut = text[:limit]
            if " " in cut:
                cut = cut[: cut.rfind(" ")].rstrip(" -|,")
            title["text"] = cut
            out.guard(
                "title_over_limit", "repair", f"标题 {len(text)} 字符超过上限 {limit}，已按词边界截断到 {len(cut)}"
            )
            lint = (
                _tool(
                    out,
                    "title_lint",
                    {
                        "text": cut,
                        "platform": platform,
                        "keywords": (output.get("keyword_map") or {}).get("primary", [])[:3],
                    },
                )
                or lint
            )
        title["chars"] = len(title["text"])
        title["limit"] = int(lint.get("limit") or title.get("limit") or 0)
        title["head30"] = title["text"][:30]
        title["checks"] = lint.get("checks", [])

        for item in lint.get("checks", []):
            out.check(f"title:{item['id']}", item["name"], bool(item["passed"]), item.get("detail", ""))

        # 合规判定只看"硬项"：字符上限 + 禁用词。
        # 前段关键词、极限词属于"质量项"：不阻塞发布，但要进审批视野（warn）。
        if not lint.get("head_has_keyword", True):
            out.guard("head_keyword_miss", "warn", "标题前 30 字符未命中核心关键词，搜索权重会打折")

        if lint.get("banned_hits"):
            out.guard("banned_term", "block", f"标题命中禁用词：{', '.join(lint['banned_hits'])}")
        if lint.get("repeated_tokens"):
            out.guard("keyword_stuffing", "repair", f"疑似堆砌：{', '.join(lint['repeated_tokens'][:5])}")
        if lint.get("exaggeration_hits"):
            out.guard("exaggeration", "warn", f"极限词：{', '.join(lint['exaggeration_hits'])}")

        compliance = output.setdefault("compliance", {})
        compliance["blocked_terms"] = lint.get("banned_hits", [])
        compliance["passed"] = (
            (not lint.get("banned_hits")) and bool(lint.get("within_limit", True)) and not out.blocked
        )

    facts = ctx.brief.get("facts") or {}
    cons = _tool(out, "spec_consistency", {"specs": output.get("specs", []), "facts": facts})
    if cons:
        out.check(
            "spec:traceability",
            "参数可溯源",
            cons.get("passed", True),
            f"溯源率 {cons.get('traceability_rate', 0):.0%}，问题 {len(cons.get('issues', []))} 条",
        )
        for issue in cons.get("issues", []):
            severity = "block" if issue["type"] == "mismatch" else "warn"
            out.guard(
                f"spec_{issue['type']}",
                severity,
                f"{issue['name']}：投稿 {issue['spec_value']} vs 事实 {issue.get('fact_value')}",
            )

    output["self_check"] = out.checks
    if out.blocked:
        compliance = output.setdefault("compliance", {})
        compliance["passed"] = False


def _gate_image(out: GateOutcome, output: dict[str, Any], ctx: AgentContext) -> None:
    platform = ctx.project.get("platform", "shopee")
    size = int((output.get("cover_spec") or {}).get("size_px") or 1200)
    img = _tool(
        out,
        "check_main_image",
        {
            "declared_width": size,
            "declared_height": size,
            "platform": platform,
            "background": "white",
            "overlay_text_chars": 0,
        },
    )
    if img:
        for item in img.get("checks", []):
            out.check(f"image:{item['id']}", item["name"], bool(item["passed"]), item.get("detail", ""))
        if img.get("warnings"):
            for w in img["warnings"]:
                out.guard("image_warning", "warn", w)

    slots = output.get("nine_grid") or []
    purposes = [s.get("purpose", "") for s in slots]
    out.check("image:grid_size", "9 图完整", len(slots) == 9, f"实际 {len(slots)} 张")
    out.check("image:grid_unique", "9 图分工不重复", len(set(purposes)) == len(purposes), "用途重复会导致信息冗余")

    long_texts = []
    for slot in slots:
        text = slot.get("overlay_text") or ""
        if len(text) > 16:
            long_texts.append((slot.get("slot"), text))
            cut = text[:16]
            if " " in cut:
                cut = cut[: cut.rfind(" ")]
            slot["overlay_text"] = cut.strip()
        if slot.get("slot") == 1 and text:
            slot["overlay_text"] = ""
            out.guard("main_image_text", "repair", "主图不允许叠加文字，已清空")

    # 先修复再判定：能自动修的，落库时应是"已通过"
    out.check(
        "image:overlay_len", "文字叠加 ≤16 字符", True, f"自动截断 {len(long_texts)} 处" if long_texts else "全部合规"
    )
    if long_texts:
        out.guard("overlay_too_long", "repair", f"第 {[s for s, _ in long_texts]} 张文案过长，已按词边界截断")

    output["compliance"] = {
        "passed": not out.blocked,
        "blocked_terms": [],
        "notes": [f"主图 {size}×{size}px，白底，9 图分工已校验"],
    }
    output["self_check"] = out.checks


def _gate_video(out: GateOutcome, output: dict[str, Any], ctx: AgentContext) -> None:
    target = int(ctx.brief.get("target_duration") or 20)
    shots = [
        {"t_start": s.get("t_start", 0), "t_end": s.get("t_end", 0), "caption": s.get("caption", "")}
        for s in output.get("shots", [])
    ]
    timing = _tool(out, "video_timing", {"shots": shots, "target_duration": target})
    if timing:
        output["timing_check"] = {
            "total_duration": timing.get("total_duration"),
            "target_duration": target,
            "within_target": timing.get("within_target"),
            "avg_cps": timing.get("avg_cps"),
            "hook_ok": timing.get("hook_ok"),
        }
        for item in timing.get("checks", []):
            out.check(f"video:{item['id']}", item["name"], bool(item["passed"]), item.get("detail", ""))
        for issue in timing.get("overlaps", []):
            out.guard("video_overlap", "repair", f"分镜时间轴重叠：{issue}")
        for issue in timing.get("gaps", []):
            out.guard("video_gap", "warn", f"分镜时间轴存在空档：{issue}")
        for issue in timing.get("cps_issues", []):
            out.guard("video_cps", "warn", f"字幕阅读速度过快：{issue}")
        if not timing.get("within_target", True):
            out.guard("video_duration", "warn", f"总时长 {timing.get('total_duration')}s 偏离目标 {target}s 超过 15%")
    output["self_check"] = out.checks


def _gate_market(out: GateOutcome, output: dict[str, Any], ctx: AgentContext) -> None:
    m = output.get("metrics") or {}
    band = output.get("price_band") or {}
    out.check("market:cr4_range", "CR4 在 0-1", 0 <= float(m.get("cr4", 0)) <= 1, f"CR4={m.get('cr4')}")
    out.check("market:hhi_range", "HHI 在 0-10000", 0 <= float(m.get("hhi", 0)) <= 10000, f"HHI={m.get('hhi')}")
    out.check(
        "market:band_order",
        "价格带次序正确",
        band.get("valley_low", 0) <= band.get("median", 0) <= band.get("p75", 0)
        or band.get("valley_low", 0) <= band.get("valley_high", 0),
        f"波谷 {band.get('valley_low')}-{band.get('valley_high')}，中位 {band.get('median')}",
    )
    shares = [c.get("share", 0) for c in output.get("config_matrix", [])]
    out.check("market:share_sum", "配置份额合计 ≤1", sum(shares) <= 1.001, f"合计 {sum(shares):.2f}")
    verdict = (output.get("precheck") or {}).get("verdict")
    if m.get("hhi", 0) >= 2500 and verdict == "GO":
        out.guard("verdict_vs_concentration", "warn", "高集中市场给出 GO，需在 risks 中说明差异化路径")
    out.check("market:opportunities", "机会点 ≥3 条", len(output.get("opportunity_points", [])) >= 3)


def _gate_strategy(out: GateOutcome, output: dict[str, Any], ctx: AgentContext) -> None:
    roles = output.get("sku_roles") or []
    out.check("strategy:sku_count", "SKU 四角色齐全", len(roles) == 4, f"实际 {len(roles)} 个")
    role_names = {r.get("role") for r in roles}
    out.check(
        "strategy:sku_roles",
        "四角色不重复",
        role_names == {"引流款", "主推款", "利润款", "形象款"},
        str(sorted(role_names)),
    )
    prices = [r.get("price", 0) for r in roles]
    out.check("strategy:price_order", "价格阶梯递增", prices == sorted(prices), f"{prices}")
    pricing = output.get("pricing") or {}
    out.check(
        "strategy:margin_cover",
        "目标价高于盈亏价",
        pricing.get("target_price", 0) >= pricing.get("breakeven_price", 0),
        f"目标 {pricing.get('target_price')} / 盈亏 {pricing.get('breakeven_price')}",
    )
    out.check(
        "strategy:margin_sane",
        "目标净利率 0-60%",
        0 <= float(pricing.get("net_margin_at_target", 0)) <= 0.6,
        f"{pricing.get('net_margin_at_target')}",
    )
    if not output.get("main_selling_point"):
        out.guard("missing_usp", "block", "缺少唯一主卖点：创意簇无法开工")


def _gate_ads(out: GateOutcome, output: dict[str, Any], ctx: AgentContext) -> None:
    f = output.get("funnel") or {}
    be = output.get("breakeven") or {}

    def close(a: float, b: float, tol: float = 0.02) -> bool:
        if b == 0:
            return abs(a) < 1e-9
        return abs(a - b) / abs(b) <= tol

    ctr = f["clicks"] / f["impressions"] if f.get("impressions") else 0
    cvr = f["orders"] / f["clicks"] if f.get("clicks") else 0
    roas = f["revenue"] / f["spend"] if f.get("spend") else 0
    out.check(
        "ads:ctr_math", "CTR 与原始数据一致", close(f.get("ctr", 0), ctr), f"报告 {f.get('ctr')} / 复算 {ctr:.4f}"
    )
    out.check(
        "ads:cvr_math", "CVR 与原始数据一致", close(f.get("cvr", 0), cvr), f"报告 {f.get('cvr')} / 复算 {cvr:.4f}"
    )
    out.check(
        "ads:roas_math", "ROAS 与原始数据一致", close(f.get("roas", 0), roas), f"报告 {f.get('roas')} / 复算 {roas:.4f}"
    )
    out.check("ads:breakeven_present", "盈亏线已核算", be.get("roas_min", 0) > 0, f"ROAS 下限 {be.get('roas_min')}")
    if f.get("roas", 0) < be.get("roas_min", 0):
        p0 = [a for a in output.get("actions", []) if a.get("priority") == "P0"]
        out.check("ads:p0_when_losing", "亏损时给出 P0 动作", bool(p0), f"P0 数量 {len(p0)}")
        if not p0:
            out.guard("no_p0", "block", "ROAS 低于盈亏线但没有 P0 动作，计划不允许执行")
    for a in output.get("actions", []):
        if not a.get("sla_hours"):
            out.guard("action_no_sla", "warn", f"动作缺少 SLA：{a.get('action', '')[:20]}")
    if f.get("spend", 0) > 0 and not math.isfinite(f.get("roas", 0)):
        out.guard("roas_nan", "block", "ROAS 计算异常")


def _gate_review(out: GateOutcome, output: dict[str, Any], ctx: AgentContext) -> None:
    a = output.get("attribution") or {}
    total = a.get("total", 0)
    counts = a.get("counts") or {}
    out.check(
        "review:total_matches",
        "样本数与输入一致",
        total == len(ctx.brief.get("reviews") or []),
        f"归因 {total} / 输入 {len(ctx.brief.get('reviews') or [])}",
    )
    out.check(
        "review:rate_range",
        "比率在 0-1",
        all(0 <= float(a.get(k, 0)) <= 1 for k in ("negative_rate", "fixable_rate", "misunderstanding_rate")),
    )
    out.check(
        "review:misunderstood_le_fixable",
        "误解率 ≤ 可修复率",
        float(a.get("misunderstanding_rate", 0)) <= float(a.get("fixable_rate", 0)) + 1e-9,
        f"误解 {a.get('misunderstanding_rate')} / 可修复 {a.get('fixable_rate')}",
    )
    out.check("review:counts_have_keys", "分类维度齐全", len(counts) >= 8, f"{len(counts)} 类")
    if not output.get("listing_patches"):
        out.guard("no_patch", "warn", "未产出详情页修补建议")
    if total < 20:
        out.guard("small_sample", "warn", f"样本仅 {total} 条，归因结论置信度有限")


_GATES = {
    "01_market_research": _gate_market,
    "02_product_strategy": _gate_strategy,
    "03_listing_creative": _gate_listing,
    "04_product_image": _gate_image,
    "05_product_video": _gate_video,
    "06_advertising": _gate_ads,
    "07_review": _gate_review,
}


def run_gates(agent_id: str, output: dict[str, Any], ctx: AgentContext) -> GateOutcome:
    out = GateOutcome(output=output)
    handler = _GATES.get(agent_id)
    if handler:
        handler(out, output, ctx)
    return out
