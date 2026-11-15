"""Assert the real cached demo and browser recovery flows on a running server.

Requires Playwright and Chrome. Run with .venv/bin/python scripts/verify_viewer.py.
"""
import argparse
import json
from pathlib import Path

from playwright.sync_api import sync_playwright


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:8000")
    parser.add_argument("--out", type=Path, default=Path("artifacts/viewer-verification"))
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    report = {"checks": [], "page_errors": [], "unexpected_http_errors": []}
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel="chrome", headless=True)
        page = browser.new_page(viewport={"width": 1600, "height": 960})
        page.on("pageerror", lambda error: report["page_errors"].append(str(error)))
        page.on("response", lambda response: report["unexpected_http_errors"].append(
            {"url": response.url, "status": response.status}) if response.status >= 400 else None)
        page.goto(args.url + "/?notour&nointro")
        page.wait_for_selector("#loader", state="hidden", timeout=180000)
        page.wait_for_selector("#inspector", state="visible", timeout=60000)
        page.wait_for_function("!document.querySelector('#catalog-status').textContent.startsWith('Loading')")
        state = page.evaluate("async () => { const {S} = await import('/static/js/state.js'); return {fid:S.fid,a:S.a,b:S.b,selected:S.selected}; }")
        field_id = state["fid"]
        summary = page.request.get(f"{args.url}/api/fields/{field_id}").json()
        assert summary["provenance"]["n_downloaded"] == 0
        assert summary["provenance"]["n_from_cache"] == len(summary["epochs"])
        assert set(summary["provenance"]["releases"]) == {"dr1", "qr3"}
        provenance = page.request.get(f"{args.url}/api/fields/{field_id}/epochs/{state['a']}/provenance").json()
        assert provenance["provenance"]["source_url"].startswith("https://irsa.ipac.caltech.edu/")
        assert len(provenance["provenance"]["response_sha256"]) == 64
        report["data"] = {"epochs": len(summary["epochs"]), "usable": sum(epoch["usable"] for epoch in summary["epochs"]),
                          "provenance": summary["provenance"], "example_observation": provenance}
        report["checks"].append("Real IRSA DR1/QR3 observations loaded entirely from cache")
        for index in (state["a"], state["b"]):
            page.wait_for_function("async index => { const img = new Image(); img.src = `/api/fields/" + field_id + "/frames/${index}.png`; await img.decode(); return img.naturalWidth > 0; }", arg=index)
        canvas = page.locator("#sky")
        page.click('[data-cmp="a"]')
        image_a = canvas.evaluate("canvas => canvas.toDataURL()")
        page.click('[data-cmp="b"]')
        image_b = canvas.evaluate("canvas => canvas.toDataURL()")
        assert image_a != image_b
        page.click('[data-cmp="blink"]')
        page.wait_for_function("expected => document.querySelector('#sky').toDataURL() === expected", arg=image_a)
        page.wait_for_function("expected => document.querySelector('#sky').toDataURL() === expected", arg=image_b)
        report["checks"].append("Blink renders both distinct epoch images")
        page.screenshot(path=str(args.out / "blink.png"))
        with page.expect_response(lambda response: "/diff.png?" in response.url and response.status == 200):
            page.click('[data-cmp="diff"]')
        page.wait_for_function("previous => {const current=document.querySelector('#sky').toDataURL(); return current!==previous[0] && current!==previous[1];}", arg=[image_a, image_b])
        page.wait_for_function("() => { const canvas=document.querySelector('#sky'); const pixels=canvas.getContext('2d').getImageData(0,0,canvas.width,canvas.height).data; let blue=0,orange=0; for(let offset=0;offset<pixels.length;offset+=4){if(pixels[offset+2]>pixels[offset]+50) blue++; if(pixels[offset]>pixels[offset+2]+50) orange++;} return blue>500 && orange>500; }")
        report["checks"].append("Difference PNG renders blue and orange residuals")
        page.screenshot(path=str(args.out / "difference.png"))
        page.click('[data-cmp="a"]')
        page.click("#insp-close")
        page.click("#zoom-fit")
        page.wait_for_timeout(300)
        target = next(source for source in summary["sources"] if source["id"] == state["selected"])
        bounds = canvas.bounding_box()
        grid = summary["grid"]
        scale = min(bounds["width"] / grid["nx"], bounds["height"] / grid["ny"]) * 0.94
        screen_x = (bounds["width"] - grid["nx"] * scale) / 2 + (target["x"] + 0.5) * scale
        screen_y = (bounds["height"] - grid["ny"] * scale) / 2 + (grid["ny"] - target["y"] - 0.5) * scale
        canvas.click(position={"x": screen_x, "y": screen_y})
        page.wait_for_selector("#inspector", state="visible")
        assert "Barnard" in page.locator("#insp-title").inner_text()
        assert "systematics-limited" in page.locator("#insp-body").inner_text()
        page.click('[data-tab="motion"]')
        assert page.locator("#insp-body svg.plot").count() >= 3
        page.screenshot(path=str(args.out / "investigation-motion.png"))
        page.click('[data-tab="obs"]')
        assert page.locator("#insp-body tbody tr").count() >= 50
        page.click('[data-tab="spectrum"]')
        assert "No reliable spectrum" in page.locator("#insp-body").inner_text()
        page.click('[data-tab="notes"]')
        page.fill("#note", "Viewer verification")
        page.click('[data-tab="overview"]')
        page.click('[data-tab="notes"]')
        assert page.locator("#note").input_value() == "Viewer verification"
        report["checks"].append("Canvas click opens Barnard investigation; all five tabs work and saturation caveat remains")
        for failed_endpoint, recovery_button in (("config", "er-demo"), ("presets", "er-retry"), ("fields", "er-demo")):
            recovery = browser.new_page()
            recovery.on("pageerror", lambda error: report["page_errors"].append(str(error)))
            endpoint = f"**/api/{failed_endpoint}"
            recovery.route(endpoint, lambda route: route.fulfill(status=404, content_type="application/json", body='{"detail":"Not Found"}'))
            recovery.goto(args.url + "/?notour&nointro")
            recovery.wait_for_selector("#er-demo")
            assert "Restart the backend" in recovery.locator("#loader").inner_text()
            recovery.unroute(endpoint)
            recovery.click("#" + recovery_button)
            recovery.wait_for_selector("#loader", state="hidden", timeout=180000)
            recovery.wait_for_selector("#inspector", state="visible", timeout=60000)
            report["checks"].append(f"Recovery from /api/{failed_endpoint} 404 via {recovery_button}")
            recovery.close()
        browser.close()
    (args.out / "report.json").write_text(json.dumps(report, indent=2))
    assert not report["page_errors"], report["page_errors"]
    assert not report["unexpected_http_errors"], report["unexpected_http_errors"]
    print(json.dumps({key: value for key, value in report.items() if key != "data"}, indent=2))


if __name__ == "__main__":
    main()
