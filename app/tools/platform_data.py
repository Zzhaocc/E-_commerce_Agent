"""平台规则参考数据（费率 / 标题字符上限 / 主图规范）。

说明：这里是**演示用快照**，生产环境应由配置中心或平台 API 拉取并带版本号。
字段来源见 docs/references.md，所有硬指标发布前须以平台后台实时规则复核。
"""

from __future__ import annotations

PLATFORM_RULES: dict[str, dict] = {
    "shopee": {
        "label": "Shopee",
        "title_limit": 120,
        "head_weight_chars": 30,
        "max_images": 9,
        "min_image_px": 500,
        "recommend_px": 1000,
        "background": "white",
        "commission_rate": 0.14,
        "payment_rate": 0.02,
        "languages": ["pt-BR", "en", "th", "vi"],
    },
    "mercadolivre": {
        "label": "Mercado Livre",
        "title_limit": 60,
        "head_weight_chars": 60,
        "max_images": 10,
        "min_image_px": 500,
        "recommend_px": 1200,
        "background": "clean",
        "commission_rate": 0.16,
        "payment_rate": 0.03,
        "languages": ["pt-BR", "es"],
    },
    "amazon": {
        "label": "Amazon",
        "title_limit": 200,
        "head_weight_chars": 80,
        "max_images": 9,
        "min_image_px": 1000,
        "recommend_px": 1600,
        "background": "pure_white",
        "commission_rate": 0.15,
        "payment_rate": 0.0,
        "languages": ["en"],
    },
    "tiktok": {
        "label": "TikTok Shop",
        "title_limit": 100,
        "head_weight_chars": 40,
        "max_images": 9,
        "min_image_px": 800,
        "recommend_px": 1080,
        "background": "any",
        "commission_rate": 0.05,
        "payment_rate": 0.02,
        "languages": ["pt-BR", "en", "es"],
    },
    "shopify": {
        "label": "Shopify",
        "title_limit": 70,
        "head_weight_chars": 70,
        "max_images": 20,
        "min_image_px": 800,
        "recommend_px": 1600,
        "background": "any",
        "commission_rate": 0.0,
        "payment_rate": 0.029,
        "languages": ["en", "pt-BR"],
    },
}

# 各平台禁用词/违规词（演示快照，真实场景应接平台违规词库 + 品牌词库）
BANNED_TERMS: dict[str, list[str]] = {
    "shopee": [
        "melhor do mundo",  # 绝对化
        "número 1",  # 排名宣称（无凭证）
        "cura",  # 医疗宣称
        "original",  # 品牌真伪宣称（无授权时）
        "mais barato do mercado",
        "grátis",  # 诱导性表述需条件
    ],
    "mercadolivre": ["envio grátis", "melhor preço", "original", "garantia vitalícia"],
    "amazon": ["best seller", "cure", "fda approved", "100% guaranteed"],
    "tiktok": ["melhor do mundo", "cura", "grátis"],
    "shopify": ["cure", "100% guaranteed"],
}

# 夸大 / 极限词（软拦截：命中则提示改写）
EXAGGERATION_TERMS = ["perfeito", "incrível", "revolucionário", "milagroso", "inigualável", "top"]
