"""04 商品主图：像素级合规校验（标准库解析文件头，不依赖 Pillow）。

为什么不在 Prompt 里问模型「这张图够不够大」：
- min_px / 推荐像素 / 1:1 / 白底 / 文字覆盖都是平台可判定的硬指标，必须读真实文件；
- 只解析 PNG/JPEG 头部的几十个字节，零第三方依赖，部署轻、可单测、结果可复现。
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.tools.base import ToolError, tool
from app.tools.platform_data import PLATFORM_RULES

_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
_JPEG_SOI = b"\xff\xd8"

# SOF0(基线) / SOF2(渐进) 等帧头标记，尺寸就写在这类段里
_JPEG_SOF_MARKERS = frozenset(
    {0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF}
)

# 白底要求：只有这两个平台强制
_WHITE_BG_PLATFORMS = frozenset({"shopee", "amazon"})
_WHITE_NAMES = frozenset(
    {
        "white",
        "pure white",
        "purewhite",
        "white background",
        "branco",
        "branco puro",
        "fundo branco",
    }
)
# 方形主图要求：这三个平台 1:1
_SQUARE_PLATFORMS = frozenset({"shopee", "amazon", "mercadolivre"})


def _resolve_platform(platform: str) -> tuple[str, dict[str, Any], list[str]]:
    """平台规则解析：未知平台回退 shopee，并把回退原因写进 warnings。"""
    key = (platform or "shopee").strip().lower()
    if key in PLATFORM_RULES:
        return key, PLATFORM_RULES[key], []
    return "shopee", PLATFORM_RULES["shopee"], [f"未知平台 {platform!r}，已回退 shopee 规则"]


def _png_size(data: bytes) -> tuple[int, int] | None:
    """PNG：签名后紧跟 IHDR，宽高在字节 16:20 / 20:24（大端）。"""
    if len(data) < 24 or not data.startswith(_PNG_SIGNATURE):
        return None
    width = int.from_bytes(data[16:20], "big")
    height = int.from_bytes(data[20:24], "big")
    if width <= 0 or height <= 0:
        return None
    return width, height


def _jpeg_size(data: bytes) -> tuple[int, int] | None:
    """JPEG：从 SOI 起遍历段，遇到 SOF0/SOF2 取高宽（大端）。"""
    if not data.startswith(_JPEG_SOI):
        return None
    total = len(data)
    offset = 2
    while offset + 1 < total:
        if data[offset] != 0xFF:
            offset += 1
            continue
        marker = data[offset + 1]
        if marker == 0xFF:
            # 填充字节，跳过一个 0xFF 继续找标记
            offset += 1
            continue
        if marker == 0x00 or marker == 0xD8 or marker == 0xD9 or 0xD0 <= marker <= 0xD7:
            # 无长度的独立标记（stuffed byte / SOI / EOI / RSTn）
            offset += 2
            continue
        if marker == 0xDA:
            # SOS 后是熵编码数据，不会再有 SOF
            break
        if offset + 4 > total:
            break
        segment_length = int.from_bytes(data[offset + 2 : offset + 4], "big")
        if segment_length < 2:
            break
        if marker in _JPEG_SOF_MARKERS and offset + 9 <= total:
            # 段载荷：精度(1) + 高(2) + 宽(2) + 颜色分量数(1)
            height = int.from_bytes(data[offset + 5 : offset + 7], "big")
            width = int.from_bytes(data[offset + 7 : offset + 9], "big")
            if width > 0 and height > 0:
                return width, height
            return None
        offset += 2 + segment_length
    return None


def _read_size(path: Path) -> tuple[int, int]:
    """读文件头拿真实像素；不支持的格式直接报错，不猜。"""
    try:
        data = path.read_bytes()
    except OSError as exc:
        raise ToolError("check_main_image", f"图片读取失败: {path}（{exc}）") from exc
    size = _png_size(data) or _jpeg_size(data)
    if size is None:
        raise ToolError(
            "check_main_image",
            f"无法解析图片尺寸: {path}（仅支持 PNG/JPEG，且文件头需完整）",
        )
    return size


def _is_whiteish(background: str) -> bool:
    """背景是否「白底」：支持名称、下划线写法和 #RGB/#RRGGBB。"""
    value = background.strip().lower().replace("_", " ")
    value = re.sub(r"\s+", " ", value)
    if value in _WHITE_NAMES:
        return True
    compact = value.replace(" ", "")
    match = re.fullmatch(r"#?([0-9a-f]{3}|[0-9a-f]{6})", compact)
    if not match:
        return False
    hex_value = match.group(1)
    if len(hex_value) == 3:
        hex_value = "".join(ch * 2 for ch in hex_value)
    channels = [int(hex_value[i : i + 2], 16) for i in (0, 2, 4)]
    return min(channels) >= 240


class CheckImageArgs(BaseModel):
    """check_main_image 入参。"""

    model_config = ConfigDict(extra="forbid")

    path: str | None = Field(default=None, description="本地图片路径；提供则从文件头读取真实像素")
    declared_width: int | None = Field(default=None, gt=0, description="无法提供文件时的声明宽度")
    declared_height: int | None = Field(default=None, gt=0, description="无法提供文件时的声明高度")
    platform: str = Field(default="shopee", description="shopee|mercadolivre|amazon|tiktok|shopify")
    background: str | None = Field(default=None, description="背景描述，如 white / pure_white / #FFFFFF / cinza")
    overlay_text_chars: int | None = Field(default=None, ge=0, description="主图叠加文字字符数")


@tool(
    "check_main_image",
    "校验主图像素下限、推荐像素、1:1 比例、白底与文字覆盖；"
    "PNG/JPEG 尺寸直接从文件头解析，不依赖 Pillow。",
    CheckImageArgs,
    tags=("04",),
)
def check_main_image(a: CheckImageArgs) -> dict[str, Any]:
    platform, rules, notes = _resolve_platform(a.platform)
    has_declared = a.declared_width is not None and a.declared_height is not None
    if a.path is None and not has_declared:
        raise ToolError("check_main_image", "缺少图片尺寸信息：请传 path 或 declared_width/height")

    warnings: list[str] = list(notes)
    declared_pair = (
        (int(a.declared_width), int(a.declared_height)) if has_declared else None
    )

    if a.path is not None:
        image_path = Path(a.path)
        if not image_path.is_file():
            raise ToolError("check_main_image", f"图片不存在: {a.path}")
        width, height = _read_size(image_path)
        source = "file"
        if declared_pair is not None and (width, height) != declared_pair:
            warnings.append(
                f"声明尺寸 {declared_pair[0]}x{declared_pair[1]} 与实际 {width}x{height} 不一致，以文件为准"
            )
    else:
        width, height = int(a.declared_width), int(a.declared_height)
        source = "declared"

    min_px = int(rules["min_image_px"])
    recommend_px = int(rules["recommend_px"])
    short_side = min(width, height)
    ratio = round(width / height, 4)

    checks: list[dict[str, Any]] = []

    min_ok = short_side >= min_px
    checks.append(
        {
            "id": "min_px",
            "name": "最小像素",
            "passed": min_ok,
            "level": "error",
            "detail": f"短边 {short_side}px / 平台下限 {min_px}px",
        }
    )

    recommend_ok = short_side >= recommend_px
    checks.append(
        {
            "id": "recommend_px",
            "name": "推荐像素",
            "passed": recommend_ok,
            "level": "warning",
            "detail": f"短边 {short_side}px / 推荐 {recommend_px}px（未达标仅提示）",
        }
    )

    if platform in _SQUARE_PLATFORMS:
        square_ok = 0.9 <= ratio <= 1.1
        aspect_detail = f"宽高比 {ratio}（平台要求 0.9~1.1）"
    else:
        square_ok = True
        aspect_detail = f"宽高比 {ratio}（{platform} 不强制 1:1）"
    checks.append(
        {"id": "aspect_ratio", "name": "主图比例", "passed": square_ok, "level": "error", "detail": aspect_detail}
    )

    if platform in _WHITE_BG_PLATFORMS and a.background is not None:
        background_ok = _is_whiteish(a.background)
        background_detail = (
            f"背景 {a.background!r} 符合白底要求"
            if background_ok
            else f"背景 {a.background!r} 不符合 {rules['background']} 要求"
        )
    elif platform in _WHITE_BG_PLATFORMS:
        background_ok = True
        background_detail = f"未提供背景信息，跳过白底校验（平台要求 {rules['background']}）"
    else:
        background_ok = True
        background_detail = f"{platform} 不强制白底"
    checks.append(
        {"id": "background", "name": "背景", "passed": background_ok, "level": "error", "detail": background_detail}
    )

    if a.overlay_text_chars is None:
        overlay_ok = True
        overlay_detail = "未提供叠加文字字符数，跳过文字覆盖校验"
    else:
        # 经验口径：主图 95 个字符约占满画面，覆盖超过 20% 会挤压商品主体
        coverage = min(1.0, a.overlay_text_chars / 95)
        overlay_ok = coverage <= 0.2
        overlay_detail = f"文字预估覆盖 {coverage:.1%}（{a.overlay_text_chars} 字符 / 95，阈值 <=20%）"
    checks.append(
        {"id": "overlay_text", "name": "文字覆盖", "passed": overlay_ok, "level": "error", "detail": overlay_detail}
    )

    for check in checks:
        if not check["passed"] and check["level"] == "warning":
            warnings.append(str(check["detail"]))

    passed = all(check["passed"] for check in checks if check["level"] == "error")
    return {
        "size": {"width": width, "height": height, "source": source},
        "checks": checks,
        "passed": passed,
        "warnings": warnings,
        "platform": platform,
    }
