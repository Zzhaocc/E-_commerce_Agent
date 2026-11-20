"""03/04/05 确定性工具的单元测试。

覆盖「能算的别让模型猜」的关键口径：
- 标题超限 / 禁用词必须直接 fail，干净葡语标题必须 pass；
- 规格比对能识别数值+单位归一后的 mismatch 与 untraceable；
- 关键词分层不把否定词混进 primary；
- 主图尺寸从 PNG 文件头解析（不依赖 Pillow）；
- 视频时间轴能发现 gap / overlap / cps 超标。
"""

from __future__ import annotations

import struct
import uuid
import zlib
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest

from app.tools.base import ToolError, get_tool
from app.tools.image import CheckImageArgs, check_main_image
from app.tools.listing import (
    KeywordClassifyArgs,
    SpecConsistencyArgs,
    TitleLintArgs,
    keyword_classify,
    spec_consistency,
    title_lint,
)
from app.tools.video import VideoTimingArgs, video_timing


def _write_png_header(path: Path, width: int, height: int) -> Path:
    """只写 PNG 签名 + IHDR（宽高所在），后面补零即可满足头部解析。"""
    ihdr = struct.pack(">II", width, height) + bytes([8, 2, 0, 0, 0])
    chunk = b"IHDR" + ihdr
    png = (
        b"\x89PNG\r\n\x1a\n"
        + struct.pack(">I", len(ihdr))
        + chunk
        + struct.pack(">I", zlib.crc32(chunk) & 0xFFFFFFFF)
        + b"\x00" * 64
    )
    path.write_bytes(png)
    return path


def _write_jpeg_header(path: Path, width: int, height: int) -> Path:
    """JPEG：SOI + APP0 + SOF0（宽高所在）+ EOI，足够头部解析。"""
    app0 = b"\xff\xe0" + struct.pack(">H", 16) + b"JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00"
    sof0 = (
        b"\xff\xc0"
        + struct.pack(">H", 17)
        + bytes([8])
        + struct.pack(">HH", height, width)
        + bytes([3])
        + b"\x01\x11\x00" * 3
    )
    path.write_bytes(b"\xff\xd8" + app0 + sof0 + b"\xff\xd9")
    return path


@pytest.fixture()
def image_file() -> Iterator[Callable[..., Path]]:
    """生成临时图片文件头。

    直接写在 tests/ 下而不是 pytest 的 tmp_path：本机沙箱会拒绝访问
    以 0700 权限创建的目录（tmp_path / mkdtemp 正是如此），
    那属于测试环境限制，不应误报成产品失败。
    """
    base = Path(__file__).resolve().parent
    created: list[Path] = []

    def make(width: int, height: int, fmt: str = "png") -> Path:
        suffix = ".jpg" if fmt == "jpeg" else ".png"
        path = base / f"_creative_{uuid.uuid4().hex[:8]}{suffix}"
        created.append(path)
        if fmt == "jpeg":
            return _write_jpeg_header(path, width, height)
        return _write_png_header(path, width, height)

    yield make
    for path in created:
        path.unlink(missing_ok=True)


# --------------------------------------------------------------------------- #
# 03 Listing：标题
# --------------------------------------------------------------------------- #
def test_title_over_limit_fails() -> None:
    text = "Patinete elétrico adulto " * 8  # 200 字符，超过 shopee 120
    result = title_lint(TitleLintArgs(text=text, platform="shopee", keywords=["patinete"]))

    assert result["chars"] > result["limit"]
    assert result["within_limit"] is False
    assert result["passed"] is False
    length_check = next(check for check in result["checks"] if check["id"] == "length")
    assert length_check["passed"] is False


def test_title_banned_word_fails() -> None:
    result = title_lint(TitleLintArgs(text="Creme para pele com cura em 7 dias", keywords=["creme"]))

    assert "cura" in result["banned_hits"]
    assert result["passed"] is False
    banned_check = next(check for check in result["checks"] if check["id"] == "banned")
    assert banned_check["passed"] is False


def test_title_clean_portuguese_passes() -> None:
    result = title_lint(
        TitleLintArgs(
            text="Organizador de cozinha em aço inox com 3 nichos",
            platform="shopee",
            keywords=["organizador", "cozinha"],
            language="pt-BR",
        )
    )

    assert result["chars"] <= result["limit"]
    assert result["head_has_keyword"] is True
    assert result["primary_position"] == 0
    assert result["banned_hits"] == []
    assert result["exaggeration_hits"] == []
    assert result["passed"] is True


def test_title_unknown_platform_falls_back_to_shopee() -> None:
    result = title_lint(TitleLintArgs(text="Organizador de cozinha", platform="temu", keywords=["organizador"]))

    assert result["platform"] == "shopee"
    assert result["notes"], "未知平台必须留下回退说明"


def test_title_all_caps_and_symbol_spam_fails() -> None:
    result = title_lint(
        TitleLintArgs(text="ORGANIZADOR DE COZINHA #1 !!! ***", keywords=["organizador"])
    )

    assert result["uppercase_ratio"] > 0.5
    assert result["symbol_count"] > 4
    assert result["passed"] is False


# --------------------------------------------------------------------------- #
# 03 Listing：规格溯源
# --------------------------------------------------------------------------- #
def test_spec_mismatch_detected() -> None:
    result = spec_consistency(
        SpecConsistencyArgs(
            specs=[
                {"name": "Material", "value": "Aço Inox", "source": "fact"},
                {"name": "Peso do produto", "value": "0,5 kg", "source": "fact"},
                {"name": "Cor", "value": "Preto", "source": "fact"},
            ],
            facts={"material": "aço inox", "Peso do produto": "500 g"},
        )
    )

    assert result["checked"] == 3
    assert result["passed"] is False
    issue_types = {issue["type"] for issue in result["issues"]}
    assert issue_types == {"mismatch", "untraceable"}
    mismatch = next(issue for issue in result["issues"] if issue["type"] == "mismatch")
    assert mismatch["name"] == "Peso do produto"
    assert result["traceability_rate"] == pytest.approx(0.667, abs=1e-3)


def test_spec_unverified_does_not_fail() -> None:
    result = spec_consistency(SpecConsistencyArgs(specs=[{"name": "Cor", "value": "Preto"}], facts={}))

    assert result["issues"][0]["type"] == "unverified"
    assert result["passed"] is True
    assert result["traceability_rate"] == 0.0


def test_spec_empty_raises_tool_error() -> None:
    with pytest.raises(ToolError):
        spec_consistency(SpecConsistencyArgs(specs=[], facts={}))


# --------------------------------------------------------------------------- #
# 03 Listing：关键词分层
# --------------------------------------------------------------------------- #
def test_keyword_classify_splits_long_tail_and_negative() -> None:
    result = keyword_classify(
        KeywordClassifyArgs(
            keywords=[
                "organizador de cozinha",
                "cozinha",
                "suporte",
                "usado",
                "organizador de cozinha",  # 重复，应去重
                "grátis",
            ]
        )
    )

    assert result["primary"] == ["cozinha", "suporte"]
    assert result["long_tail"] == ["organizador de cozinha"]
    assert result["negative"] == ["usado", "grátis"]
    assert result["counts"] == {"primary": 2, "long_tail": 1, "negative": 2}
    assert result["total"] == 5
    assert result["truncated"] == 0


def test_keyword_classify_top_n_limits_primary() -> None:
    result = keyword_classify(KeywordClassifyArgs(keywords=["a", "b", "c", "d", "e", "f"], top_n=3))

    assert result["primary"] == ["a", "b", "c"]
    assert result["truncated"] == 3
    assert result["total"] == 6


# --------------------------------------------------------------------------- #
# 04 主图：PNG 文件头解析与像素/背景/文字校验
# --------------------------------------------------------------------------- #
def test_image_400x400_fails_shopee_min_size(image_file: Callable[..., Path]) -> None:
    path = image_file(400, 400)
    result = check_main_image(CheckImageArgs(path=str(path), platform="shopee"))

    assert result["size"] == {"width": 400, "height": 400, "source": "file"}
    min_check = next(check for check in result["checks"] if check["id"] == "min_px")
    recommend_check = next(check for check in result["checks"] if check["id"] == "recommend_px")
    assert min_check["passed"] is False
    assert recommend_check["passed"] is False
    assert result["passed"] is False
    assert result["warnings"], "推荐像素不达标必须进 warnings"


def test_image_1200x1200_passes_shopee(image_file: Callable[..., Path]) -> None:
    path = image_file(1200, 1200)
    result = check_main_image(CheckImageArgs(path=str(path), platform="shopee", background="white"))

    assert result["size"]["width"] == 1200
    assert result["size"]["height"] == 1200
    assert result["size"]["source"] == "file"
    assert result["passed"] is True
    assert result["warnings"] == []


def test_image_gray_background_and_overlay_text_fail(image_file: Callable[..., Path]) -> None:
    path = image_file(1200, 1200)
    result = check_main_image(
        CheckImageArgs(
            path=str(path),
            platform="shopee",
            background="cinza escuro",
            overlay_text_chars=60,
        )
    )

    assert result["passed"] is False
    failed_ids = {check["id"] for check in result["checks"] if not check["passed"]}
    assert {"background", "overlay_text"} <= failed_ids


def test_image_declared_size_used_without_file() -> None:
    result = check_main_image(CheckImageArgs(declared_width=1200, declared_height=1200, platform="shopee"))

    assert result["size"] == {"width": 1200, "height": 1200, "source": "declared"}
    assert result["passed"] is True


def test_image_jpeg_header_parsed(image_file: Callable[..., Path]) -> None:
    path = image_file(600, 600, "jpeg")
    result = check_main_image(CheckImageArgs(path=str(path), platform="shopee"))

    assert result["size"] == {"width": 600, "height": 600, "source": "file"}
    # 600 >= 500 下限，但 < 1000 推荐值：pass 且留 warning
    assert result["passed"] is True
    assert len(result["warnings"]) == 1


def test_image_missing_size_raises() -> None:
    with pytest.raises(ToolError):
        check_main_image(CheckImageArgs())
    with pytest.raises(ToolError):
        check_main_image(CheckImageArgs(path="definitely-missing.png"))


# --------------------------------------------------------------------------- #
# 05 视频：时间轴与字幕速度
# --------------------------------------------------------------------------- #
def test_video_timing_clean_timeline_passes() -> None:
    shots = [
        {"t_start": 0, "t_end": 5, "caption": "Sobe ladeira"},
        {"t_start": 5, "t_end": 12, "caption": "Motor 500W"},
        {"t_start": 12, "t_end": 20, "caption": "35 km reais"},
    ]
    result = video_timing(VideoTimingArgs(shots=shots, target_duration=20))

    assert result["total_duration"] == 20
    assert result["within_target"] is True
    assert result["gaps"] == []
    assert result["overlaps"] == []
    assert result["cps_issues"] == []
    assert result["hook_ok"] is True
    assert result["passed"] is True


def test_video_timing_detects_gap() -> None:
    shots = [
        {"t_start": 0, "t_end": 5, "caption": "Hook"},
        {"t_start": 5.8, "t_end": 12, "caption": "Motor"},
        {"t_start": 12, "t_end": 20, "caption": "Fim"},
    ]
    result = video_timing(VideoTimingArgs(shots=shots, target_duration=20))

    assert len(result["gaps"]) == 1
    assert result["gaps"][0]["gap"] == pytest.approx(0.8)
    assert result["overlaps"] == []
    assert result["passed"] is False


def test_video_timing_detects_overlap() -> None:
    shots = [
        {"t_start": 0, "t_end": 5, "caption": "Hook"},
        {"t_start": 4.4, "t_end": 12, "caption": "Motor"},
        {"t_start": 12, "t_end": 20, "caption": "Fim"},
    ]
    result = video_timing(VideoTimingArgs(shots=shots, target_duration=20))

    assert len(result["overlaps"]) == 1
    assert result["overlaps"][0]["overlap"] == pytest.approx(0.6)
    assert result["gaps"] == []
    assert result["passed"] is False


def test_video_timing_detects_cps_violation() -> None:
    shots = [
        {"t_start": 0, "t_end": 3, "caption": "a" * 90},  # 30 cps > 18
        {"t_start": 3, "t_end": 20, "caption": "ok"},
    ]
    result = video_timing(VideoTimingArgs(shots=shots, target_duration=20, max_cps=18))

    assert len(result["cps_issues"]) == 1
    assert result["cps_issues"][0]["cps"] == pytest.approx(30.0)
    assert result["passed"] is False
    assert result["hook_ok"] is True


def test_video_timing_ignores_sub_50ms_offset() -> None:
    shots = [
        {"t_start": 0, "t_end": 5, "caption": "A"},
        {"t_start": 5.05, "t_end": 12, "caption": "B"},
        {"t_start": 12, "t_end": 20, "caption": "C"},
    ]
    result = video_timing(VideoTimingArgs(shots=shots, target_duration=20))

    assert result["gaps"] == []
    assert result["overlaps"] == []


def test_video_timing_missing_hook_fails() -> None:
    shots = [
        {"t_start": 3.5, "t_end": 8, "caption": ""},
        {"t_start": 8, "t_end": 20, "caption": "Fim"},
    ]
    result = video_timing(VideoTimingArgs(shots=shots, target_duration=20))

    assert result["hook_ok"] is False
    assert result["passed"] is False


def test_video_timing_invalid_shot_raises() -> None:
    with pytest.raises(ToolError):
        video_timing(VideoTimingArgs(shots=[{"t_start": 5, "t_end": 5, "caption": ""}]))


# --------------------------------------------------------------------------- #
# 注册表
# --------------------------------------------------------------------------- #
def test_tools_registered_with_expected_tags() -> None:
    assert get_tool("title_lint").tags == ("03",)
    assert get_tool("spec_consistency").tags == ("03",)
    assert get_tool("keyword_classify").tags == ("03",)
    assert get_tool("check_main_image").tags == ("04",)
    assert get_tool("video_timing").tags == ("05",)
