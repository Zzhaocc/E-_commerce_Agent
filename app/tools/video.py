"""05 商品视频：分镜时间轴与字幕阅读速度校验。

视频节奏是转化的第一道门槛，这几项必须是可计算的：
- 分镜之间不能有黑洞（gap）也不能叠帧（overlap），容差 0.05s；
- 总时长偏离目标超过 15%，投放端会被截断或显得拖沓；
- 字幕 cps（字符/秒）超过上限就来不及读，必须拆句或缩短文案；
- 前 3 秒没有字幕钩子，完播率直接崩。
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.tools.base import ToolError, tool

# 时间轴容差：0.05s 内的误差视为剪辑抖动，不算 gap/overlap
_TOLERANCE = 0.05
# 与目标时长的相对偏差上限
_TARGET_TOLERANCE = 0.15
# 钩子最晚出现时间（秒）
_HOOK_DEADLINE = 3.0
# 浮点比较保护，避免 5.05 - 5.0 被误判成 0.0500000000000007 > 0.05
_EPSILON = 1e-9


class VideoTimingArgs(BaseModel):
    """video_timing 入参。"""

    model_config = ConfigDict(extra="forbid")

    shots: list[dict[str, Any]] = Field(
        default_factory=list,
        description='分镜列表，每项 {"t_start": float, "t_end": float, "caption": str}',
    )
    target_duration: int = Field(default=20, ge=1, le=300, description="目标总时长（秒）")
    max_cps: float = Field(default=18.0, gt=0, le=100, description="字幕阅读速度上限（字符/秒）")


@tool(
    "video_timing",
    "校验分镜时间轴：总时长偏差、gap/overlap、字幕 cps 与前 3 秒钩子，返回逐项 checks 与总 passed。",
    VideoTimingArgs,
    tags=("05",),
)
def video_timing(a: VideoTimingArgs) -> dict[str, Any]:
    if not a.shots:
        raise ToolError("video_timing", "shots 不能为空：至少提供 1 个分镜")

    parsed: list[dict[str, Any]] = []
    for idx, shot in enumerate(a.shots):
        if not isinstance(shot, dict) or "t_start" not in shot or "t_end" not in shot:
            raise ToolError("video_timing", f"shots[{idx}] 缺少 t_start/t_end")
        try:
            start = float(shot["t_start"])
            end = float(shot["t_end"])
        except (TypeError, ValueError) as exc:
            raise ToolError("video_timing", f"shots[{idx}] 的 t_start/t_end 必须是数字") from exc
        if end <= start:
            raise ToolError("video_timing", f"shots[{idx}] 的 t_end({end}) 必须大于 t_start({start})")
        parsed.append(
            {
                "index": idx + 1,
                "start": start,
                "end": end,
                "caption": str(shot.get("caption") or ""),
            }
        )

    gaps: list[dict[str, Any]] = []
    overlaps: list[dict[str, Any]] = []
    for previous, current in zip(parsed, parsed[1:]):
        delta = current["start"] - previous["end"]
        if delta > _TOLERANCE + _EPSILON:
            gaps.append(
                {
                    "shot": current["index"],
                    "gap": round(delta, 3),
                    "from": round(previous["end"], 3),
                    "to": round(current["start"], 3),
                }
            )
        elif -delta > _TOLERANCE + _EPSILON:
            overlaps.append(
                {
                    "shot": current["index"],
                    "overlap": round(-delta, 3),
                    "from": round(current["start"], 3),
                    "to": round(previous["end"], 3),
                }
            )

    # 总时长按时间轴跨度计算：首镜开始到末镜结束（gap 会体现为跨度变长，overlap 会变短）
    total_duration = round(max(shot["end"] for shot in parsed) - min(shot["start"] for shot in parsed), 3)

    target = float(a.target_duration)
    within_target = abs(total_duration - target) / target <= _TARGET_TOLERANCE

    cps_issues: list[dict[str, Any]] = []
    caption_chars = 0
    for shot in parsed:
        duration = shot["end"] - shot["start"]
        chars = len(shot["caption"])
        caption_chars += chars
        cps = chars / duration
        if cps > a.max_cps:
            cps_issues.append(
                {
                    "shot": shot["index"],
                    "cps": round(cps, 2),
                    "chars": chars,
                    "duration": round(duration, 3),
                }
            )
    avg_cps = round(caption_chars / total_duration, 2) if total_duration > 0 else 0.0

    first = parsed[0]
    hook_ok = bool(first["caption"].strip()) and first["start"] < _HOOK_DEADLINE

    checks: list[dict[str, Any]] = [
        {
            "id": "duration",
            "name": "总时长",
            "passed": within_target,
            "detail": f"总时长 {total_duration}s / 目标 {target:g}s（允许 ±15%）",
        },
        {
            "id": "gaps",
            "name": "分镜空洞",
            "passed": not gaps,
            "detail": "分镜间隔均在 0.05s 容差内"
            if not gaps
            else f"发现 {len(gaps)} 处空洞：" + ", ".join(f"镜{g['shot']}前 {g['gap']}s" for g in gaps),
        },
        {
            "id": "overlaps",
            "name": "分镜重叠",
            "passed": not overlaps,
            "detail": "分镜无重叠"
            if not overlaps
            else f"发现 {len(overlaps)} 处重叠：" + ", ".join(f"镜{o['shot']}重叠 {o['overlap']}s" for o in overlaps),
        },
        {
            "id": "cps",
            "name": "字幕阅读速度",
            "passed": not cps_issues,
            "detail": f"平均 {avg_cps} cps（上限 {a.max_cps:g}）"
            + (
                ""
                if not cps_issues
                else "，超标：" + ", ".join(f"镜{i['shot']} {i['cps']}cps" for i in cps_issues)
            ),
        },
        {
            "id": "hook",
            "name": "前 3 秒钩子",
            "passed": hook_ok,
            "detail": "首镜有字幕且 3s 内开始"
            if hook_ok
            else "首镜缺少字幕或晚于 3s 才开始（完播率风险）",
        },
    ]

    return {
        "total_duration": total_duration,
        "target_duration": a.target_duration,
        "within_target": within_target,
        "gaps": gaps,
        "overlaps": overlaps,
        "cps_issues": cps_issues,
        "avg_cps": avg_cps,
        "hook_ok": hook_ok,
        "checks": checks,
        "passed": all(check["passed"] for check in checks),
    }
