"""Capture design-review screenshots of the running app at several widths.

    .venv/bin/python scripts/ui_screens.py --url http://127.0.0.1:8001 --out artifacts/ui-screens/after

Requires Playwright + installed Google Chrome.  Screens: observatory, time
machine (playback), investigation tabs, Discover/Learn/Help/Explore, and a
loading state.  Also records horizontal overflow and console errors.
"""
import argparse
import json
from pathlib import Path

from playwright.sync_api import sync_playwright

SIZES = {"desktop": (1440, 900), "laptop": (1280, 800), "tablet": (900, 1180), "mobile": (390, 844)}


def ready(page):
    page.wait_for_selector("#loader", state="hidden", timeout=180000)
    page.wait_for_function("async () => { const {S} = await import('/static/js/state.js'); return !!S.field; }", timeout=60000)
    page.wait_for_timeout(1200)


def overflow(page):
    return page.evaluate("() => document.documentElement.scrollWidth - document.documentElement.clientWidth")


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--url", default="http://127.0.0.1:8001")
    ap.add_argument("--out", type=Path, default=Path("artifacts/ui-screens"))
    ap.add_argument("--sizes", default=",".join(SIZES))
    ap.add_argument("--full", action="store_true", help="also capture secondary views at every size")
    args = ap.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    report = {"errors": [], "overflow": {}}
    with sync_playwright() as pw:
        browser = pw.chromium.launch(channel="chrome", headless=True)
        for name in args.sizes.split(","):
            w, h = SIZES[name]
            ctx = browser.new_context(viewport={"width": w, "height": h}, device_scale_factor=2 if w < 800 else 1)
            page = ctx.new_page()
            page.on("pageerror", lambda e, n=name: report["errors"].append(f"{n}: {e}"))
            page.on("console", lambda m, n=name: report["errors"].append(f"{n} console: {m.text}") if m.type == "error" else None)
            page.goto(args.url + "/?nointro")
            ready(page)
            page.screenshot(path=str(args.out / f"{name}-1-observatory.png"))
            report["overflow"][f"{name}-observatory"] = overflow(page)
            if name == "desktop" or args.full:
                page.click("#target-btn"); page.wait_for_timeout(300)
                page.screenshot(path=str(args.out / f"{name}-1b-targets.png"))
                page.click("#layers-btn"); page.wait_for_timeout(300)
                page.screenshot(path=str(args.out / f"{name}-1c-layers.png"))
                page.keyboard.press("Escape")
                for preset in ("clean", "research"):
                    page.click(f'[data-visual="{preset}"]'); page.wait_for_timeout(300)
                    page.screenshot(path=str(args.out / f"{name}-1d-{preset}.png"))
                page.click('[data-visual="discovery"]')
                page.click("#zoom-in"); page.click("#zoom-in"); page.click("#zoom-in"); page.wait_for_timeout(200)
                page.screenshot(path=str(args.out / f"{name}-1e-zoom-presentation.png"))
                page.click("#layers-btn"); page.click('[data-render="science"]'); page.keyboard.press("Escape"); page.wait_for_timeout(200)
                page.screenshot(path=str(args.out / f"{name}-1f-zoom-science.png"))
                page.click("#layers-btn"); page.click('[data-render="presentation"]'); page.keyboard.press("Escape")
                page.click("#zoom-fit"); page.click('[data-cmp="diff"]'); page.wait_for_timeout(800)
                page.screenshot(path=str(args.out / f"{name}-1g-difference.png"))
                page.click("#dock-expand"); page.wait_for_timeout(300)
                page.screenshot(path=str(args.out / f"{name}-1h-dock-expanded.png"))
                page.click("#dock-expand"); page.click('[data-cmp="a"]')
            # Time machine: play a few frames then pause
            page.keyboard.press("p")
            page.wait_for_timeout(1600)
            page.screenshot(path=str(args.out / f"{name}-2-playing.png"))
            page.keyboard.press("p")
            for tab in ("motion", "spectrum", "obs"):
                loc = page.locator(f'[data-tab="{tab}"]')
                if loc.count() and loc.first.is_visible():
                    loc.first.click(); page.wait_for_timeout(500)
                    if name in ("desktop", "mobile") or args.full:
                        page.screenshot(path=str(args.out / f"{name}-3-{tab}.png"), full_page=(w < 800))
            if name == "desktop" or args.full:
                for view in ("discover", "learn", "help", "explore"):
                    btn = page.locator(f'[data-view="{view}"]')
                    if btn.count() and btn.first.is_visible():
                        btn.first.click(); page.wait_for_timeout(2500 if view == "explore" else 600)
                        page.screenshot(path=str(args.out / f"{name}-4-{view}.png"))
                        report["overflow"][f"{name}-{view}"] = overflow(page)
            ctx.close()
        # loading state + intro on desktop
        ctx = browser.new_context(viewport={"width": 1440, "height": 900})
        page = ctx.new_page()
        page.goto(args.url + "/?intro")
        page.wait_for_timeout(1400)
        page.screenshot(path=str(args.out / "desktop-0-intro.png"))
        ctx.close()
        browser.close()
    (args.out / "report.json").write_text(json.dumps(report, indent=2))
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
