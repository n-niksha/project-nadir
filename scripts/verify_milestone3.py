"""Browser acceptance tests and timings; real cached data, no mocked science."""
import json
import argparse
import time
from pathlib import Path

from playwright.sync_api import sync_playwright


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://127.0.0.1:8000")
    args = parser.parse_args()
    output = Path("artifacts/milestone3/browser")
    output.mkdir(parents=True, exist_ok=True)
    report = {"checks": [], "errors": [], "http_errors": [], "timings_ms": {}}
    state_js = "async () => { const {S}=await import('/static/js/state.js'); return {fid:S.fid,frame:S.frame,a:S.a,b:S.b,playing:S.playing,mode:S.cmp,selected:S.selected,detail:S.detail,field:S.field,overlays:S.overlays}; }"
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel="chrome", headless=True)
        page = browser.new_page(viewport={"width":1600,"height":1000})
        page.on("pageerror", lambda error: report["errors"].append(str(error)))
        page.on("response", lambda response: report["http_errors"].append([response.status,response.url]) if response.status >= 400 else None)
        started = time.perf_counter()
        page.goto(args.url + "/?nointro&notour")
        page.wait_for_selector("#inspector",state="visible",timeout=180000)
        page.wait_for_function("!document.querySelector('#catalog-status').textContent.startsWith('Loading')",timeout=60000)
        report["timings_ms"]["demo_ready"] = round((time.perf_counter()-started)*1000,2)
        initial = page.evaluate(state_js)
        assert initial["field"]["provenance"]["n_downloaded"] == 0
        assert page.locator(".tl-epoch").count() == len(initial["field"]["epochs"])
        assert "gap" in page.locator(".coverage").inner_text()
        report["checks"].append("Cached real demo; every observation listed and linear time gaps labelled")
        canvas = page.locator("#sky")
        before = canvas.evaluate("canvas=>canvas.toDataURL()")
        page.click("#zoom-in")
        assert canvas.evaluate("canvas=>canvas.toDataURL()") != before
        bounds = canvas.bounding_box()
        page.mouse.move(bounds["x"]+100,bounds["y"]+150)
        page.mouse.down()
        page.mouse.move(bounds["x"]+150,bounds["y"]+180,steps=5)
        page.mouse.up()
        page.click("#zoom-fit")
        for preset in ("clean","research","discovery"):
            page.click(f'[data-visual="{preset}"]')
            state = page.evaluate(state_js)
            assert state["overlays"]["sources"] == (preset != "clean")
            assert state["overlays"]["gaia"] == (preset == "research")
            page.screenshot(path=str(output/f"{preset}.png"))
        report["checks"].append("Zoom, drag-pan and all three visualization presets")
        unavailable = next(epoch for epoch in initial["field"]["epochs"] if not epoch["usable"])
        page.locator(f'.tl-epoch[data-i="{unavailable["index"]}"]').click()
        assert "unavailable" in page.locator("#toast").inner_text()
        available = next(epoch for epoch in initial["field"]["epochs"] if epoch["usable"] and abs(epoch["wavelength_um"]-initial["field"]["epochs"][initial["a"]]["wavelength_um"])>0.05)
        page.locator(f'.tl-epoch[data-i="{available["index"]}"]').click()
        state = page.evaluate(state_js)
        assert state["frame"] == available["index"] and not state["playing"]
        page.click("#step-next")
        page.click('[data-cmp="play"]')
        frames = page.evaluate("""async () => {
          const {S}=await import('/static/js/state.js');
          const {epochList}=await import('/static/js/playback.js');
          const node=document.querySelector('.tl-epoch'), seen=[];
          for(let step=0;step<8;step++){await new Promise(resolve=>setTimeout(resolve,180));seen.push(S.frame);}
          return {seen,allowed:epochList(S.field,S.a,S.playbackMode).map(epoch=>epoch.index),sameNode:node===document.querySelector('.tl-epoch')};
        }""")
        assert len(set(frames["seen"])) >= 3 and set(frames["seen"]) <= set(frames["allowed"])
        assert frames["sameNode"]
        page.click("#play")
        stopped = page.evaluate(state_js)["frame"]
        page.wait_for_timeout(400)
        assert page.evaluate(state_js)["frame"] == stopped
        page.select_option("#playback-mode","spectral")
        page.locator("#speed").fill("8")
        assert page.locator("#speed-out").inner_text() == "8/s"
        page.click("#step-next")
        assert "not temporal" in page.locator("#playback-note").inner_text()
        report["checks"].append("Any epoch selection; unavailable reasons; matched playback, pause, spectral mode, stepping and speed")
        report["timings_ms"]["cached_step_mean"] = page.evaluate("""async () => {
          const samples=[]; for(let index=0;index<15;index++) {const start=performance.now();document.querySelector('#step-next').click();await new Promise(requestAnimationFrame);samples.push(performance.now()-start);} return samples.reduce((sum,value)=>sum+value,0)/samples.length;
        }""")
        for mode in ("a","b","overlay","diff","blink"):
            page.click(f'[data-cmp="{mode}"]')
            page.wait_for_timeout(200)
        page.click('[data-cmp="a"]')
        page.click("#frame-b")
        state = page.evaluate(state_js)
        assert state["a"] != state["b"]
        page.locator("#explanation summary").click()
        assert f'{initial["detail"]["fit"]["pm_total"]:.2f}' in page.locator("#explanation-body").inner_text()
        page.click('[data-explain="research"]')
        assert "not independent validation" in page.locator("#explanation-body").inner_text()
        page.screenshot(path=str(output/"explanation.png"))
        report["checks"].append("A/B, overlay, difference, blink and deterministic explanations from actual source values")
        page.locator("#explanation summary").click()
        page.click('[data-view="discover"]')
        page.locator('[data-discovery-tier="systematics_limited"]').click()
        assert page.locator(f'#sheet [data-sid="{initial["selected"]}"]').count() == 0
        page.locator('[data-discovery-tier="systematics_limited"]').click()
        page.locator(f'#sheet [data-sid="{initial["selected"]}"]').click()
        assert page.evaluate(state_js)["selected"] == initial["selected"]
        page.click('[data-tab="notes"]')
        page.fill("#note","Milestone 3 persisted observation")
        page.reload()
        page.wait_for_selector("#note",timeout=30000)
        assert page.locator("#note").input_value() == "Milestone 3 persisted observation"
        report["checks"].append("Discovery filtering and selection; notes survive reload")
        page.click('.preset[data-id="vanmaanen"]')
        page.wait_for_function("document.querySelector('.preset[data-id=vanmaanen]').classList.contains('active') && document.querySelector('#loader').hidden",timeout=180000)
        page.click('[data-tab="spectrum"]')
        page.wait_for_selector("#spec-load, #spec-readout",timeout=60000)
        started = time.perf_counter()
        if page.locator('#spec-load:not([data-network="true"])').count():
            page.click('#spec-load:not([data-network="true"])')
        page.wait_for_selector("#spec-readout",timeout=180000)
        report["timings_ms"]["cached_spectrum"] = round((time.perf_counter()-started)*1000,2)
        spectrum_state = page.evaluate(state_js)
        assert spectrum_state["field"]["provenance"]["n_downloaded"] == 0
        assert page.locator('#insp-body .plot [data-point-info]').count() >= 6
        page.locator('#insp-body .plot [data-point-info]').first.focus()
        assert "mJy" in page.locator("#spec-readout").inner_text()
        page.check("#feat")
        assert "not detections" in page.locator("#insp-body").inner_text()
        assert "not an effective temperature" in page.locator("#insp-body").inner_text()
        page.screenshot(path=str(output/"spectrum.png"))
        report["checks"].append("van Maanen cached spectrum, uncertainty inspection, provenance, qualified temperature and illustrative features")
        for width in (900,400):
            page.set_viewport_size({"width":width,"height":1000})
            page.wait_for_timeout(300)
            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth+1")
            page.locator("#stage").scroll_into_view_if_needed()
            page.screenshot(path=str(output/f"sky-{width}.png"))
            page.locator("#inspector").scroll_into_view_if_needed()
            page.screenshot(path=str(output/f"investigation-{width}.png"))
        report["checks"].append("900px and 400px layouts without page overflow")
        browser.close()
    (output/"report.json").write_text(json.dumps(report,indent=2))
    assert not report["errors"], report["errors"]
    assert not report["http_errors"], report["http_errors"]
    print(json.dumps(report,indent=2))


if __name__ == "__main__":
    main()
