"""用 Playwright 给控制台截图（开发辅助，不在运行依赖里）。

    python scripts/screenshots.py                 # 需要本机已安装 playwright + chromium
    python -m playwright install chromium

产物：docs/screenshots/console-*.jpg
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

BASE = "http://127.0.0.1:8000"
OUT = Path(__file__).resolve().parent.parent / "docs" / "screenshots"
CHROME = os.getenv("CHROME_PATH") or str(
    Path(os.environ.get("LOCALAPPDATA", "")) / "ms-playwright" / "chromium-1252" / "chrome-win64" / "chrome.exe"
)


def main() -> int:
    from playwright.sync_api import sync_playwright

    OUT.mkdir(parents=True, exist_ok=True)
    errors: list[str] = []

    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=CHROME if Path(CHROME).exists() else None)
        page = browser.new_page(viewport={"width": 1440, "height": 900}, device_scale_factor=2)
        page.on("pageerror", lambda e: errors.append(f"pageerror: {e}"))
        page.on("console", lambda m: errors.append(f"console.{m.type}: {m.text}") if m.type == "error" else None)

        page.goto(BASE, wait_until="networkidle")
        page.wait_for_timeout(1500)

        shots = [
            ("market", "市场分析"),
            ("strategy", "产品策略"),
            ("listing", "Listing 创意"),
            ("image", "主图与详情图"),
            ("ads", "广告优化（含闭环复测）"),
            ("review", "评论分析"),
            ("trace", "Trace 与回流"),
            ("publish", "发布包"),
        ]
        for tab, label in shots:
            page.click(f'#tabs [data-tab="{tab}"]')
            page.wait_for_timeout(400)
            path = OUT / f"console-{tab}.jpg"
            page.screenshot(path=str(path), type="jpeg", quality=88)
            print(f"  ✓ {label} → {path.name}")

        page.click('.nav-item[data-view="eval"]')
        page.wait_for_timeout(300)
        if page.query_selector("#btn-eval-run"):
            page.click("#btn-eval-run")
        page.wait_for_timeout(1600)
        page.screenshot(path=str(OUT / "console-eval.jpg"), type="jpeg", quality=88)
        print("  ✓ 评测门禁 → console-eval.jpg")

        page.click('.nav-item[data-view="metrics"]')
        page.wait_for_timeout(1200)
        page.screenshot(path=str(OUT / "console-metrics.jpg"), type="jpeg", quality=88)
        print("  ✓ 平台指标 → console-metrics.jpg")

        browser.close()

    if errors:
        print("\n浏览器错误：")
        for err in errors[:10]:
            print("  -", err)
        return 1
    print("\n全部截图完成，无浏览器错误")
    return 0


if __name__ == "__main__":
    sys.exit(main())
