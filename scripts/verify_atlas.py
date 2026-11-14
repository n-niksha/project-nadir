"""Verify the optional external IRSA atlas in real Chrome."""
import json
import argparse
from pathlib import Path

from playwright.sync_api import sync_playwright


parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--url", default="http://127.0.0.1:8000")
args = parser.parse_args()

with sync_playwright() as playwright:
    browser = playwright.chromium.launch(channel="chrome", headless=True, args=["--use-angle=swiftshader", "--enable-unsafe-swiftshader"])
    page = browser.new_page(viewport={"width": 1600, "height": 960})
    report = {"tiles": [], "errors": []}
    page.on("response", lambda response: report["tiles"].append([response.status, response.url]) if "spherex_allsky_cube/Norder" in response.url else None)
    page.on("pageerror", lambda error: report["errors"].append(str(error)))
    page.goto(args.url + "/?notour")
    page.wait_for_selector("#loader", state="hidden", timeout=180000)
    page.click("[data-view=explore]")
    page.wait_for_timeout(10000)
    report["initial_count"] = len(report["tiles"])
    page.locator("#slice").fill("60")
    page.wait_for_timeout(10000)
    report["label"] = page.locator("#slice-out").inner_text()
    output = Path("artifacts/milestone3/atlas")
    output.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(output / "channel60.png"))
    (output / "report.json").write_text(json.dumps(report, indent=2))
    assert any(status == 200 and "_23.png" in url for status, url in report["tiles"])
    assert any(status == 200 and "_60.png" in url for status, url in report["tiles"])
    assert "3.118" in report["label"]
    assert not report["errors"], report["errors"]
    assert all(status == 200 for status, url in report["tiles"])
    print(json.dumps(report, indent=2))
    browser.close()
