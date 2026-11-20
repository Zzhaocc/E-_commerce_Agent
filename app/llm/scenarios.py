"""Mock Provider 的确定性产出构造器。

定位：**离线可跑的规则型 Agent**，不是"假数据"。
- 数值一律来自真实工具计算结果（evidence），不硬编码；
- 文案来自商品知识库，接真实模型时由模型生成同样结构；
- 所有输出仍要过 Pydantic 契约 + 护栏，和真实模型走完全相同的链路。
"""

from __future__ import annotations

from typing import Any

from app.llm.knowledge import find_kb


def money(v: float, cur: str = "BRL") -> str:
    return f"{cur} {v:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def _clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


# --------------------------------------------------------------------------- #
# 01 市场分析
# --------------------------------------------------------------------------- #
def build_market(payload: dict[str, Any]) -> dict[str, Any]:
    ev = payload.get("evidence", {})
    m = ev["market_metrics"]
    cr4, hhi, demand = m["cr4"], m["hhi"], m["demand_index"]

    score = int(_clamp(100 - hhi / 40 - cr4 * 35 + demand * 25, 15, 96))
    verdict = "GO" if score >= 70 else "CAUTION" if score >= 50 else "NO_GO"

    reasons = [
        f"CR4 = {cr4:.0%}，HHI = {hhi:.0f}（{m['concentration_label']}），头部占比决定能否靠单点差异化切入",
        f"月销量合计 {m['total_monthly_sales']:,} 件，需求指数 {demand:.2f}（2 万件/月记为 1.0）",
        f"加权价格中位数 {money(m['price_band']['median'], m['currency'])}，"
        f"p25–p75 区间 {money(m['price_band']['p25'], m['currency'])}–{money(m['price_band']['p75'], m['currency'])}",
    ]
    if m.get("avg_rating") is not None:
        reasons.append(f"竞品均分 {m['avg_rating']:.2f}，评论池 {m['review_pool']:,} 条，说明口碑门槛{'高' if m['avg_rating'] >= 4.5 else '中等'}")

    band = m["price_band"]
    rationale = (
        f"{money(band['valley_low'], m['currency'])}–{money(band['valley_high'], m['currency'])} 区间内，"
        f"SKU 密度为相邻价格带的最低点（供需效率 {band['valley_efficiency']:.2f}），且仍有成交，"
        f"属于可切入而不用正面比价的价格缝隙"
    )

    opportunity = [
        f"价格缝隙：{money(band['recommended_low'], m['currency'])}–{money(band['recommended_high'], m['currency'])}"
        f" 可避开与头部主推价 {money(band['median'], m['currency'])} 的正面比价",
        f"集中度{'高' if hhi >= 2500 else '中等'}：不拼大盘词，用长尾配置词拿精准流量",
        "评论池已过千条，说明需求被验证；差评集中在可修补项时，详情页改造成本最低",
    ]

    config_matrix = m.get("config_matrix") or []
    if config_matrix:
        top = config_matrix[0]
        recommended = top["combo"]
        opportunity.append(
            f"配置组合「{top['combo']}」占销量 {top['share']:.0%}，均价 {money(top['avg_price'], m['currency'])}，可作为主推配置"
        )
    else:
        recommended = "按目标价反推配置，先上单品验证"

    risks = []
    if hhi >= 2500:
        risks.append("HHI ≥ 2500：头部若降价，跟随战会直接击穿净利，必须留出券余量")
    if demand < 0.4:
        risks.append("需求指数偏低：上架后前 14 天若曝光起不来，应及时止损而非加预算")
    risks.append("价格带结论依赖竞品导出完整性，样本 < 20 条时置信度按 0.7 折算")

    return {
        "precheck": {"verdict": verdict, "score": score, "reasons": reasons},
        "metrics": {
            "competitor_count": m["competitor_count"],
            "cr4": cr4,
            "hhi": hhi,
            "avg_price": m["avg_price"],
            "gmv_estimate": m["gmv_estimate"],
            "demand_index": demand,
        },
        "price_band": {
            "p25": band["p25"],
            "median": band["median"],
            "p75": band["p75"],
            "valley_low": band["valley_low"],
            "valley_high": band["valley_high"],
            "recommended_low": band["recommended_low"],
            "recommended_high": band["recommended_high"],
            "rationale": rationale,
            "valley_efficiency": band.get("valley_efficiency", 0),
        },
        "price_buckets": [
            {
                "low": b["low"],
                "high": b["high"],
                "sku": b["sku"],
                "sales": b["sales"],
                "supply_index": b.get("supply_index", 0),
                "demand_index": b.get("demand_index", 0),
                "efficiency": b.get("efficiency", 0),
            }
            for b in (band.get("buckets") or [])
        ],
        "config_matrix": [
            {"combo": c["combo"], "share": c["share"], "avg_price": c["avg_price"], "sales": c["sales"]}
            for c in config_matrix
        ],
        "opportunity_points": opportunity,
        "recommended_config": recommended,
        "risks": risks,
        "confidence": round(_clamp(0.62 + m["competitor_count"] / 120, 0.55, 0.92), 2),
    }


# --------------------------------------------------------------------------- #
# 02 产品策略
# --------------------------------------------------------------------------- #
def build_strategy(payload: dict[str, Any]) -> dict[str, Any]:
    inp, ev = payload["input"], payload.get("evidence", {})
    econ = ev["unit_economics"]
    ladder = ev["sku_ladder"]
    market = inp.get("market_analysis") or {}
    _, kb = find_kb(inp["product"])

    brand_config = inp.get("config_options") or []
    if not brand_config and market.get("config_matrix"):
        brand_config = [c["combo"] for c in market["config_matrix"][:3]]

    config_ladder = []
    for i, item in enumerate(ladder["ladder"]):
        config_ladder.append(
            {
                "name": f"{kb.get('pt_name', inp['product'])} · {item['role']}",
                "config": brand_config[i] if i < len(brand_config) else item["role"],
                "price": item["price"],
                "role": item["role"],
            }
        )

    risks = []
    if not econ["coupon_budget_is_safe"]:
        risks.append(
            f"券预算 {econ['coupon_budget']:.0%} 超过可承受折扣 {econ['allowed_discount_rate']:.0%}，"
            "上活动即亏损，需先降成本或提价"
        )
    risks.append("净利率按当前广告费率测算，实际 ACOS 若高于设定值，净利同比下滑")
    risks.append("配置阶梯的价差需在详情页解释清楚，否则用户只买最便宜档")

    return {
        "positioning": kb["positioning"],
        "main_selling_point": kb["selling_point"],
        "secondary_points": kb["secondary_points"][:3],
        "config_ladder": config_ladder,
        "pricing": {
            "breakeven_price": econ["breakeven_price"],
            "target_price": econ["target_price"],
            "net_margin_at_target": econ["net_margin_at_target"],
            "coupon_headroom": econ["coupon_headroom"],
            "allowed_discount_rate": econ["allowed_discount_rate"],
            "formula_version": econ["formula"]["version"],
        },
        "sku_roles": [
            {
                "role": item["role"],
                "name": f"SKU {i + 1} · {item['role']}",
                "price": item["price"],
                "rationale": item["rationale"],
            }
            for i, item in enumerate(ladder["ladder"])
        ],
        "risks": risks,
        "confidence": 0.84,
    }


# --------------------------------------------------------------------------- #
# 03 Listing
# --------------------------------------------------------------------------- #
def _split_keywords(keywords: list[str]) -> tuple[list[str], list[str], list[str]]:
    negative = ["usado", "usada", "defeito", "grátis", "peças", "infantil", "brinquedo"]
    primary, long_tail, neg = [], [], []
    seen = set()
    for kw in keywords:
        k = (kw or "").strip()
        if not k or k.lower() in seen:
            continue
        seen.add(k.lower())
        if any(n in k.lower() for n in negative):
            neg.append(k)
        elif len(k.split()) >= 3:
            long_tail.append(k)
        else:
            primary.append(k)
    return primary[:6], long_tail[:6], neg[:4]


def build_listing(payload: dict[str, Any]) -> dict[str, Any]:
    inp, ev = payload["input"], payload.get("evidence", {})
    rules = ev["platform_rules"]["rules"]
    limit = rules["title_limit"]
    _, kb = find_kb(inp["product"])

    strategy = inp.get("strategy") or {}
    selling = strategy.get("main_selling_point") or kb["selling_point"]

    # 标题：品牌/品类词打头（前 30 字符是权重最高的位置），再接核心卖点与配置
    core = kb["pt_name"]
    suffix = " | ".join(
        [
            "Sobe Ladeira 15°" if "ladeira" in selling.lower() else selling.split(":")[0][:38],
            "Nota Fiscal",
            "Garantia 12 Meses",
        ]
    )
    title = f"{core} - {suffix}"

    specs_src = dict(inp.get("specs") or {})
    specs = []
    for name, value in (kb.get("specs") or {}).items():
        if not specs_src or name in specs_src:
            specs.append({"name": name, "value": specs_src.get(name, value), "source": "fact"})
    for name, value in specs_src.items():
        if name not in (kb.get("specs") or {}):
            specs.append({"name": name, "value": value, "source": "fact"})

    keywords = list(inp.get("keywords") or []) + list(kb.get("keywords") or [])
    primary, long_tail, negative = _split_keywords(keywords)

    return {
        "keyword_map": {
            "primary": primary,
            "long_tail": long_tail,
            "negative": negative or kb.get("negative_keywords", []),
        },
        "title": {"text": title, "chars": len(title), "limit": limit, "head30": title[:30], "checks": []},
        "bullets": kb["bullets"][:5],
        "description": {
            "intro": f"{kb['pt_name']} — {selling}.",
            "sections": [{"title": t, "body": b} for t, b in kb["description_sections"]],
            "package_list": kb["package_list"],
            "search_terms": primary + long_tail,
        },
        "specs": specs[:10],
        "compliance": {"passed": True, "blocked_terms": [], "notes": []},
        "self_check": [],
        "confidence": 0.86,
    }


# --------------------------------------------------------------------------- #
# 04 图片
# --------------------------------------------------------------------------- #
def build_image(payload: dict[str, Any]) -> dict[str, Any]:
    inp = payload["input"]
    _, kb = find_kb(inp["product"])
    strategy = inp.get("strategy") or {}
    selling = strategy.get("main_selling_point") or kb["selling_point"]

    grid = []
    for i, (purpose, scene, prompt) in enumerate(kb["nine_grid"], start=1):
        overlay = kb["overlay_texts"][i - 1] if i - 1 < len(kb["overlay_texts"]) else ""
        if i == 2 and not overlay:
            overlay = selling[:28]
        grid.append(
            {
                "slot": i,
                "purpose": purpose,
                "scene": scene,
                "overlay_text": overlay,
                "prompt": prompt + ", high resolution, no watermark, no logo",
                "risk": "文字叠加过多会被平台判定为牛皮癣主图，保持 ≤20% 面积" if overlay else "",
            }
        )

    return {
        "cover_spec": {
            "ratio": "1:1",
            "size_px": 1200,
            "background": "branco puro (#FFFFFF)",
            "subject_ratio": "85%",
            "max_text_area": "20%",
            "format": "JPG/PNG ≤ 2MB",
        },
        "nine_grid": grid,
        "compliance": {"passed": True, "blocked_terms": [], "notes": []},
        "self_check": [],
        "confidence": 0.82,
    }


# --------------------------------------------------------------------------- #
# 05 视频
# --------------------------------------------------------------------------- #
def build_video(payload: dict[str, Any]) -> dict[str, Any]:
    inp = payload["input"]
    _, kb = find_kb(inp["product"])
    target = int(inp.get("target_duration") or 20)
    raw = kb["video"]["shots"]
    src_total = max(1, raw[-1][1])
    scale = target / src_total

    shots, captions, narration = [], [], []
    for t0, t1, visual, voice, caption in raw:
        shots.append(
            {
                "t_start": round(t0 * scale, 1),
                "t_end": round(t1 * scale, 1),
                "visual": visual,
                "voiceover": voice,
                "caption": caption,
            }
        )
        captions.append(caption)
        narration.append(voice)

    return {
        "hook": kb["video"]["hook"],
        "shots": shots,
        "narration": " ".join(narration),
        "captions": captions,
        "timing_check": {},
        "self_check": [],
        "confidence": 0.8,
    }


# --------------------------------------------------------------------------- #
# 06 广告
# --------------------------------------------------------------------------- #
_SYMPTOM = {
    "low_ctr": ("点击率低于类目下限，曝光在浪费", "主图/标题与搜索意图不匹配，或出价抢到了不精准的词"),
    "low_cvr": ("进店后转化不足", "详情页没有回答决策问题：尺寸、兼容性、售后"),
    "cpa_over": ("获客成本高于可承受上限", "出价过高或落地页承接弱，导致 CPA 吃掉全部毛利"),
    "roas_below": ("整体 ROAS 低于盈亏线", "投放结构问题：低效计划占比过高，预算未集中"),
    "zero_order_spend": ("有花费零成交的计划在持续消耗", "关键词或人群过宽，系统在学习期持续试错"),
    "low_aov": ("客单价覆盖不了成本带", "只推了引流款，未做配置升级与关联购买"),
}


def build_ads(payload: dict[str, Any]) -> dict[str, Any]:
    ev = payload.get("evidence", {})
    m = ev["ad_metrics"]
    funnel, be = m["funnel"], m["breakeven"]

    diagnosis, actions = [], []
    for sig in m["signals"]:
        symptom, root = _SYMPTOM.get(sig["code"], (sig["code"], "需人工复核"))
        diagnosis.append(
            {
                "layer": sig["layer"],
                "symptom": f"{symptom}（{sig['code']}）",
                "root_cause": root,
                "evidence": sig["evidence"][:3] or [f"{sig['code']} 触发"],
            }
        )
        if sig["code"] == "low_ctr":
            action = "换主图第 1 张（场景图替代白底图），标题前 30 字符换成搜索词，观察 3 天 CTR"
            impact = "CTR 从当前水平回到 1% 以上，同等曝光下点击量 +25%~40%"
        elif sig["code"] == "low_cvr":
            action = "详情页补尺寸/兼容性/售后三张图，评价区置顶安装视频"
            impact = "CVR 提升 0.5–1.2 个百分点，CPA 同比下降"
        elif sig["code"] == "cpa_over":
            action = "把出价降到可承受 CPC 的 0.8 倍，同时关掉连续 3 天零转化词"
            impact = f"CPA 回落到 ≤ {money(be['cpa_max'])}"
        elif sig["code"] == "roas_below":
            action = "暂停 ROAS 后 30% 的计划，预算集中到 Top2 计划"
            impact = f"整体 ROAS 回到盈亏线 {be['roas_min']:.2f} 以上"
        elif sig["code"] == "zero_order_spend":
            action = "零成交计划降价 20% 并限定精准匹配，72 小时无单即关停"
            impact = f"回收约 {m['waste_ratio']:.0%} 的无效花费"
        else:
            action = "上配置升级套餐，把主推款客单抬到利润款区间"
            impact = "AOV +8%~15%，同等 CPA 下 ROAS 同比例上升"

        actions.append(
            {
                "priority": sig["severity"],
                "action": action,
                "expected_impact": impact,
                "owner": "广告运营",
                "sla_hours": 24 if sig["severity"] == "P0" else 72,
            }
        )

    if not actions:
        actions.append(
            {
                "priority": "P2",
                "action": "维持当前结构，按周观察 ROAS，达标后按 20% 幅度放量",
                "expected_impact": "ROAS 稳定在盈亏线上方",
                "owner": "广告运营",
                "sla_hours": 168,
            }
        )
        diagnosis.append(
            {
                "layer": "转化",
                "symptom": "漏斗各层均未触发硬阈值",
                "root_cause": "当前结构健康，瓶颈在规模而非效率",
                "evidence": [f"CTR {funnel['ctr']:.2%} / CVR {funnel['cvr']:.2%} / ROAS {funnel['roas']:.2f}"],
            }
        )

    return {
        "funnel": {
            "impressions": funnel["impressions"],
            "clicks": funnel["clicks"],
            "orders": funnel["orders"],
            "spend": funnel["spend"],
            "revenue": funnel["revenue"],
            "ctr": funnel["ctr"],
            "cvr": funnel["cvr"],
            "cpc": funnel["cpc"],
            "cpa": funnel["cpa"],
            "roas": funnel["roas"],
            "aov": funnel["aov"],
        },
        "breakeven": {
            "aov": be["aov"],
            "contribution_margin": be["contribution_margin"],
            "cpa_max": be["cpa_max"],
            "cpc_max": be["cpc_max"],
            "roas_min": be["roas_min"],
            "current_roas_vs_target": be["current_roas_vs_target"],
        },
        "diagnosis": diagnosis,
        "actions": actions,
        "budget_realloc": m.get("budget_realloc") or [],
        "confidence": 0.87,
    }


# --------------------------------------------------------------------------- #
# 07 评论
# --------------------------------------------------------------------------- #
_PATCH_TARGET = {
    "描述不符": ("listing", "详情页首屏加「尺寸/材质/适用场景」对照表，把容易误解的参数前置"),
    "包装破损": ("packaging", "外箱加蜂窝纸护角，主图补包装清单图，降低运输损坏争议"),
    "使用难度": ("listing", "详情页加 30 秒安装视频与图文步骤，售后话术同步"),
    "尺寸容量": ("image", "尺寸图从第 4 张提到第 2 张，并在图上标注「与常见物品对比」"),
    "质量做工": ("listing", "详情页增加做工细节图与质检说明，避免只靠文案承诺"),
    "物流时效": ("listing", "页面明确发货时效与承运商，减少「催发货」类差评"),
    "电池续航": ("listing", "详情页标注测试条件（载重/温度/速度），避免预期落差"),
    "客服售后": ("listing", "详情页加售后入口与响应时效说明"),
}


def build_review(payload: dict[str, Any]) -> dict[str, Any]:
    ev = payload.get("evidence", {})
    stats = ev["review_stats"]
    attr = stats["attribution"]

    listing_patches, image_patches = [], []
    for item in stats["top_complaint"]:
        cat = item["point"]
        target, change = _PATCH_TARGET.get(cat, ("listing", "在详情页补充该类问题的说明"))
        patch = {
            "target": target,
            "change": change,
            "reason": f"「{cat}」在差评中出现 {item['mentions']} 次，属于可修补项",
            "evidence_count": max(1, item["mentions"]),
        }
        if target in {"image"}:
            image_patches.append(patch)
        elif target in {"packaging"}:
            listing_patches.append({**patch, "target": "listing"})
            image_patches.append({**patch, "target": "image"})
        else:
            listing_patches.append(patch)

    if not listing_patches:
        listing_patches.append(
            {
                "target": "listing",
                "change": "在详情页补充「常见问题」段落，前置尺寸与售后说明",
                "reason": "差评样本未形成集中归因，先用 FAQ 兜底",
                "evidence_count": 1,
            }
        )

    return {
        "attribution": {
            "counts": attr["counts"],
            "total": attr["total"],
            "negative_rate": attr["negative_rate"],
            "fixable_rate": attr["fixable_rate"],
            "misunderstanding_rate": attr["misunderstanding_rate"],
        },
        "top_praise": [p["point"] for p in stats["top_praise"]] or ["质量做工"],
        "top_complaint": [p["point"] for p in stats["top_complaint"]] or ["其他"],
        "listing_patches": listing_patches[:4],
        "image_patches": image_patches[:3],
        "confidence": 0.83,
    }


BUILDERS = {
    "01_market_research": build_market,
    "02_product_strategy": build_strategy,
    "03_listing_creative": build_listing,
    "04_product_image": build_image,
    "05_product_video": build_video,
    "06_advertising": build_ads,
    "07_review": build_review,
}
