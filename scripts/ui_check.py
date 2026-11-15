#!/usr/bin/env python
"""Drive the real web app in Chrome (Playwright) and capture screenshots of
the key states.  Requires a running server and Google Chrome.

    python scripts/ui_check.py --url http://127.0.0.1:8000 --out artifacts/screens
"""
import argparse
import json
import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://127.0.0.1:8000")
    ap.add_argument("--out", type=Path, default=Path("artifacts/screens"))
    ap.add_argument("--steps", default="all")
    a = ap.parse_args()
    a.out.mkdir(parents=True, exist_ok=True)
    log = {"console_errors": [], "network_errors": [], "atlas_tiles": [], "steps": []}

    def step(name, page, fn=None, wait=0.0):
        t = time.perf_counter()
        if fn:
            fn()
        if wait:
            page.wait_for_timeout(wait * 1000)
        page.screenshot(path=str(a.out / f"{name}.png"))
        log["steps"].append({"name": name, "s": round(time.perf_counter() - t, 2)})
        print(f"  {name}: {log['steps'][-1]['s']} s", flush=True)

    with sync_playwright() as p:
        browser = p.chromium.launch(channel="chrome", headless=True, args=["--use-angle=swiftshader", "--enable-unsafe-swiftshader"])
        page = browser.new_page(viewport={"width": 1600, "height": 960}, device_scale_factor=1)
        page.on("console", lambda m: m.type in ("error",) and log["console_errors"].append(m.text))
        page.on("pageerror", lambda e: log["console_errors"].append(f"pageerror: {e}"))
        page.on("requestfailed", lambda r: log["network_errors"].append({"url": r.url, "error": r.failure}))
        page.on("response", lambda r: r.status >= 400 and log["network_errors"].append({"url": r.url, "status": r.status}))
        page.on("response", lambda r: "spherex_allsky_cube/Norder" in r.url and r.status == 200 and log["atlas_tiles"].append(r.url))
        t0 = time.perf_counter()
        page.goto(a.url + "/?intro&notour", wait_until="domcontentloaded")
        step("01_intro", page, wait=0.8)
        if page.locator("#intro").is_visible():
            page.keyboard.press("Escape")
        page.wait_for_selector("#loader", state="hidden", timeout=180000)
        page.wait_for_function("document.querySelector('#inspector') && !document.querySelector('#inspector').hidden", timeout=60000)
        log["time_to_interactive_s"] = round(time.perf_counter() - t0, 2)
        print("time to interactive", log["time_to_interactive_s"])
        page.wait_for_timeout(1500)
        step("02_demo_blink", page)
        page.click("[data-cmp=diff]")
        step("03_difference", page, wait=1.0)
        page.click("[data-cmp=overlay]")
        step("04_overlay", page, wait=0.6)
        page.click("[data-cmp=play]")
        step("05_animate", page, wait=1.5)
        page.click("[data-cmp=a]")
        # zoom on the selected source
        page.click("#zoom-in"); page.click("#zoom-in")
        step("06_zoom", page, wait=0.5)
        page.click(".tabs [data-tab=motion]")
        step("07_motion_tab", page, wait=0.8)
        page.click(".tabs [data-tab=obs]")
        step("08_observations_tab", page, wait=0.5)
        page.click(".tabs [data-tab=spectrum]")
        step("09_spectrum_saturated", page, wait=0.8)
        page.click(".tabs [data-tab=notes]")
        page.fill("#note", "Test note from the UI check.")
        step("10_notes", page, wait=0.3)
        # catalog overlays
        page.wait_for_function("!document.querySelector('#catalog-status').textContent.startsWith('Loading')", timeout=400000)
        page.check("[data-ov=gaia]"); page.check("[data-ov=simbad]")
        page.click("#zoom-fit")
        step("11_catalogs", page, wait=0.6)
        page.click("[data-view=discover]")
        step("12_discover", page, wait=0.5)
        page.click("[data-view=learn]")
        step("13_learn", page, wait=0.5)
        page.click("[data-view=help]")
        step("14_help", page, wait=0.3)
        page.click("[data-view=explore]")
        step("15_explore", page, wait=8)
        assert log["atlas_tiles"], "No actual IRSA atlas tiles loaded"
        page.locator("#slice").fill("60")
        page.wait_for_timeout(3000)
        assert "3.118" in page.locator("#slice-out").inner_text()
        assert any("_60.png" in url for url in log["atlas_tiles"]), "Channel 60 tiles did not load"
        step("15b_explore_channel", page)
        page.click("[data-view=time]")
        # a non-demo preset with an unsaturated mover: van Maanen's star
        page.click("#target-btn"); page.click("#target-pop .preset[data-id=vanmaanen]")
        page.wait_for_selector("#loader", state="visible", timeout=10000)
        step("16_loading", page, wait=0.4)
        page.wait_for_selector("#loader", state="hidden", timeout=600000)
        step("17_vanmaanen", page, wait=1.5)
        page.click(".tabs [data-tab=motion]")
        step("18_vanmaanen_motion", page, wait=0.8)
        page.click(".tabs [data-tab=spectrum]")
        page.wait_for_timeout(500)
        if page.query_selector('#spec-load:not([data-network="true"])'):
            page.click('#spec-load:not([data-network="true"])')
        page.wait_for_selector(".insp-body svg.plot", timeout=600000)
        step("19_vanmaanen_spectrum", page, wait=0.8)
        # tablet / phone layouts
        page.set_viewport_size({"width": 900, "height": 1100})
        step("20_tablet", page, wait=0.8)
        page.set_viewport_size({"width": 400, "height": 860})
        step("21_phone", page, wait=0.8)
        browser.close()
    (a.out / "ui_check.json").write_text(json.dumps(log, indent=1))
    print("console errors:", log["console_errors"])
    return 1 if log["console_errors"] else 0


if __name__ == "__main__":
    sys.exit(main())
