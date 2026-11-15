"""Browser verification of NADIR Journeys (Stage A show + Stage B lessons).

    .venv/bin/python scripts/verify_journey.py --url http://127.0.0.1:8001 --out artifacts/journey

Uses only cached data (the spectral step runs with cached_only=true).  Checks:
start from the dossier, pause/resume, skip/previous, real frames only, the
spectrum hand-over to a different (labelled) star, end actions, exact restore
of the observatory, Explore-it-yourself, a failing scene, rapid input, reduced
motion, mobile layout and a non-Barnard journey.  Screenshots every scene.
Exit code 1 if any check fails.
"""
import argparse
import json
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

RESULTS = []


def check(name, ok, detail=""):
    RESULTS.append({"check": name, "ok": bool(ok), "detail": detail})
    print(("PASS " if ok else "FAIL ") + name + (f" — {detail}" if detail else ""))


def boot(page, url):
    page.goto(url + "/?nointro&notour")
    page.wait_for_selector("#loader", state="hidden", timeout=180000)
    page.wait_for_function("() => window.NADIR && NADIR.S.detail && NADIR.S.detail.id === 0", timeout=60000)
    page.wait_for_timeout(600)


def scene_ready(page, i, timeout=60000):
    page.wait_for_function(f"() => NADIR.journey.i === {i} && NADIR.journey.ready", timeout=timeout)


def wait_t(page, ms, timeout=60000):
    page.wait_for_function(f"() => !NADIR.journey.active || NADIR.journey.t >= {ms} || NADIR.journey.ended", timeout=timeout)


def goto_scene(page, scene_id):
    idx = page.evaluate(f"NADIR.journey.scenes.findIndex(s => s.id === '{scene_id}')")
    page.click(f'#jr-progress [data-scene="{idx}"]')
    scene_ready(page, idx)
    return idx


FRAME_SPY = """() => { window.__frames = new Set(); window.__spy = setInterval(() => { const b = NADIR.sky.current().base; if (b != null) window.__frames.add(b); }, 40); }"""


def run_desktop(pw, url, out, errors):
    browser = pw.chromium.launch(channel="chrome", headless=True)
    page = browser.new_page(viewport={"width": 1440, "height": 900})
    page.on("pageerror", lambda e: errors.append(f"desktop: {e}"))
    page.on("console", lambda m: errors.append(f"desktop console: {m.text}") if m.type == "error" else None)
    boot(page, url)

    # a deliberately non-default observatory state, to prove the exit restores it
    page.click('[data-tab="obs"]'); page.click('[data-cmp="diff"]'); page.click("#zoom-in"); page.wait_for_timeout(300)
    before = page.evaluate("() => ({ sel: NADIR.S.selected, tab: NADIR.S.tab, cmp: NADIR.S.cmp, a: NADIR.S.a, b: NADIR.S.b, k: NADIR.sky.k, ox: NADIR.sky.ox, ov: JSON.stringify(NADIR.S.overlays) })")
    cta = page.locator("#take-journey")
    check("dossier offers 'Take a journey'", cta.is_visible() and "Take a journey" in cta.inner_text(), cta.inner_text().replace("\n", " · "))
    page.screenshot(path=str(out / "desktop-0-dossier.png"))

    page.evaluate(FRAME_SPY)
    cta.click()
    scene_ready(page, 0)
    check("journey layer opens over the observatory", page.evaluate("document.body.classList.contains('journey-on') && !document.getElementById('journey').hidden && getComputedStyle(document.querySelector('.topbar')).display === 'none'"))
    check("play button has focus (keyboard ready)", page.evaluate("document.activeElement.id") == "jr-play")
    check("Barnard gets the full 5-scene show", page.evaluate("NADIR.journey.scenes.map(s => s.id).join(',')") == "wonder,time,measure,light,observer")
    check("first caption + real-data stamp", len(page.inner_text("#jr-caption")) > 10 and "Real SPHEREx exposure" in page.inner_text("#jr-stamp"), page.inner_text("#jr-caption"))

    # pause / resume via keyboard
    wait_t(page, 1000)
    page.keyboard.press(" ")
    t0 = page.evaluate("NADIR.journey.t"); page.wait_for_timeout(1500); t1 = page.evaluate("NADIR.journey.t")
    check("Space pauses: clock frozen", t0 == t1 and not page.evaluate("NADIR.journey.playing"), f"t {t0:.0f} → {t1:.0f}")
    page.click("#jr-play"); page.wait_for_timeout(700)
    check("resume advances the clock", page.evaluate("NADIR.journey.t") > t1 and page.evaluate("NADIR.journey.playing"))
    wait_t(page, 11500)
    page.screenshot(path=str(out / "desktop-1-wonder.png"))

    # scene 2 via skip; only real frames ever shown
    page.click("#jr-next"); scene_ready(page, 1)
    wait_t(page, 5000); page.screenshot(path=str(out / "desktop-2-time.png"))
    wait_t(page, 12500); page.screenshot(path=str(out / "desktop-2b-blink.png"))
    frames = page.evaluate("() => { clearInterval(window.__spy); const ok = new Set(NADIR.S.field.epochs.filter(e => e.usable && e.has_frame !== false).map(e => e.index)); return { shown: [...window.__frames], bad: [...window.__frames].filter(i => !ok.has(i)) }; }")
    check("only real registered exposures were displayed", not frames["bad"] and len(frames["shown"]) >= 2, f"{len(frames['shown'])} distinct frames, invalid {frames['bad']}")
    check("main playback timers stay idle during the show", not page.evaluate("NADIR.S.playing"))

    # scene 3: measurement + history
    page.click("#jr-next"); scene_ready(page, 2)
    wait_t(page, 11000); page.screenshot(path=str(out / "desktop-3-measure.png"))
    card = page.inner_text("#jr-panel")
    check("measurement card: value, 1σ, Gaia, provenance tags", "″/yr" in card and "1σ" in card and "Gaia DR3" in card and "MEASURED" in card.upper() and "CATALOG" in card.upper())
    wait_t(page, 21500); page.screenshot(path=str(out / "desktop-3b-history.png"))
    check("catalog history labelled as catalog", "catalog" in page.inner_text("#jr-stamp").lower())

    # scene 4: Barnard is saturated -> labelled hand-over to a different star
    page.click("#jr-next"); scene_ready(page, 3, timeout=120000)
    wait_t(page, 6000)
    check("saturation limitation narrated", "too bright" in page.inner_text("#jr-caption") or "saturat" in page.inner_text("#jr-panel").lower())
    wait_t(page, 14500); page.screenshot(path=str(out / "desktop-4-light.png"))
    panel = page.inner_text("#jr-panel")
    check("spectrum demo is explicitly a different star", "different star" in panel.lower() and "different star" in page.inner_text("#jr-stamp").lower(), panel.split("\n")[0])
    check("spectrum chart with inferred colour model", page.locator("#jr-panel svg.plot").count() == 1 and "INFERRED" in panel.upper())

    # rapid input must not tangle scenes
    for key in ("ArrowRight", "ArrowLeft", "ArrowRight", "ArrowLeft", "ArrowLeft"):
        page.keyboard.press(key)
    page.wait_for_timeout(1500)
    st = page.evaluate("() => ({ i: NADIR.journey.i, ready: NADIR.journey.ready, fired: NADIR.journey.fired, n: NADIR.journey.beats.length })")
    check("rapid skip/back settles on one consistent scene", st["ready"] and 0 <= st["i"] <= 4 and st["fired"] <= st["n"], json.dumps(st))

    # final scene + end actions
    goto_scene(page, "observer"); wait_t(page, 9000); page.screenshot(path=str(out / "desktop-5-observer.png"))
    page.wait_for_selector("#jr-end", state="visible", timeout=30000)
    page.screenshot(path=str(out / "desktop-6-end.png"))
    check("end offers Explore / Evidence / Return", page.locator("#jr-end button").count() == 3)

    # Return to observatory -> exact restore
    page.click('[data-jr-action="return"]'); page.wait_for_timeout(500)
    after = page.evaluate("() => ({ sel: NADIR.S.selected, tab: NADIR.S.tab, cmp: NADIR.S.cmp, a: NADIR.S.a, b: NADIR.S.b, k: NADIR.sky.k, ox: NADIR.sky.ox, ov: JSON.stringify(NADIR.S.overlays) })")
    check("exit restores selection, tab, mode, pair, overlays, camera", after == before, f"before {before} after {after}")
    check("journey fully stopped", page.evaluate("!NADIR.journey.active && document.getElementById('journey').hidden && !document.body.classList.contains('journey-on') && NADIR.sky.journey === null"))
    page.screenshot(path=str(out / "desktop-7-restored.png"))

    # Esc exits mid-scene too
    page.click("#take-journey"); scene_ready(page, 0); page.keyboard.press("Escape"); page.wait_for_timeout(300)
    check("Esc exits at any time", not page.evaluate("NADIR.journey.active"))

    # View the scientific evidence
    page.click("#take-journey"); scene_ready(page, 0); goto_scene(page, "observer")
    page.click("#jr-next"); page.wait_for_selector("#jr-end", state="visible")
    page.click('[data-jr-action="evidence"]'); page.wait_for_timeout(800)
    check("'View the scientific evidence' opens Barnard's Motion tab", page.evaluate("NADIR.S.selected === 0 && NADIR.S.tab === 'motion' && !document.getElementById('inspector').hidden"))

    # Explore it yourself (Stage B reuses the scenes)
    page.click("#take-journey"); scene_ready(page, 0); goto_scene(page, "observer")
    page.click("#jr-next"); page.wait_for_selector("#jr-end", state="visible")
    page.click('[data-jr-action="explore"]'); page.wait_for_selector("#lessons", state="visible")
    n = page.evaluate("document.querySelectorAll('#lessons .lesson-pips i').length")
    page.click('#lessons [data-l="next"]'); page.click('#lessons [data-run="0"]'); page.wait_for_timeout(600)
    check("Explore-it-yourself lessons drive the real instruments", n == 5 and page.evaluate("NADIR.S.cmp") == "blink", f"{n} lessons, cmp={page.evaluate('NADIR.S.cmp')}")
    page.screenshot(path=str(out / "desktop-8-lessons.png"))
    for _ in range(3):
        page.click('#lessons [data-l="next"]')
    page.fill("#lesson-note", "Teal stars hold still; Barnard's Star jumps north.")
    check("observation notebook saves into the Notes tab storage", "jumps north" in page.evaluate("Object.keys(localStorage).filter(k => k.startsWith('nadir:notes:')).map(k => localStorage.getItem(k)).join(' ')"))
    page.click('#lessons [data-l="close"]')

    # a non-Barnard object: compact, data-driven journey
    page.evaluate("NADIR.startJourney(15)"); scene_ready(page, 0)
    ids = page.evaluate("NADIR.journey.scenes.map(s => s.id).join(',')")
    check("rejected source gets a compact journey without a motion claim", "measure" not in ids and page.inner_text("#jr-name").startswith("A closer look"), ids)
    page.click("#jr-next"); scene_ready(page, 1); wait_t(page, 4000)
    page.screenshot(path=str(out / "desktop-9-other-object.png"))
    page.keyboard.press("Escape")
    check("other-object exit restores Barnard selection", page.evaluate("NADIR.S.selected") == 0)
    browser.close()


def run_failure(pw, url, out, errors):
    browser = pw.chromium.launch(channel="chrome", headless=True)
    page = browser.new_page(viewport={"width": 1280, "height": 800})
    local = []
    page.on("pageerror", lambda e: local.append(str(e)))
    page.route("**/spectrum", lambda route: route.fulfill(status=503, body="offline"))
    boot(page, url)
    page.click("#take-journey"); scene_ready(page, 0)
    goto_scene(page, "light"); wait_t(page, 10000)
    cap = page.inner_text("#jr-caption")
    check("failed spectrum fetch -> honest narration, no crash", ("leave the light unread" in cap or "skipped" in cap) and not local, cap)
    page.screenshot(path=str(out / "laptop-failure-light.png"))
    page.click("#jr-next"); scene_ready(page, 4)
    check("journey continues after the failed scene", page.evaluate("NADIR.journey.scenes[NADIR.journey.i].id") == "observer")
    errors += [f"failure-run: {e}" for e in local]
    browser.close()


def run_reduced(pw, url, out, errors):
    browser = pw.chromium.launch(channel="chrome", headless=True)
    ctx = browser.new_context(viewport={"width": 1280, "height": 800}, reduced_motion="reduce")
    page = ctx.new_page()
    page.on("pageerror", lambda e: errors.append(f"reduced: {e}"))
    boot(page, url)
    page.click("#take-journey"); scene_ready(page, 0); wait_t(page, 4000)
    check("reduced motion: camera moves are instant", page.evaluate("NADIR.sky._fly === null && !NADIR.sky._animating"))
    check("reduced motion: no chapter flourish", page.evaluate("getComputedStyle(document.getElementById('jr-flourish')).display") == "none")
    goto_scene(page, "measure"); wait_t(page, 6000)
    check("reduced motion: reveals are immediate", page.evaluate("NADIR.sky.journey.trail && NADIR.sky.journey.trail.ms === 0"))
    page.screenshot(path=str(out / "laptop-reduced-measure.png"))
    ctx.close(); browser.close()


def run_mobile(pw, url, out, errors):
    browser = pw.chromium.launch(channel="chrome", headless=True)
    ctx = browser.new_context(viewport={"width": 390, "height": 844}, device_scale_factor=2, is_mobile=True, has_touch=True)
    page = ctx.new_page()
    page.on("pageerror", lambda e: errors.append(f"mobile: {e}"))
    boot(page, url)
    page.locator("#take-journey").scroll_into_view_if_needed(); page.click("#take-journey")
    scene_ready(page, 0); wait_t(page, 9000)
    page.screenshot(path=str(out / "mobile-1-wonder.png"))
    ov = page.evaluate("document.documentElement.scrollWidth - document.documentElement.clientWidth")
    ctrl = page.evaluate("(() => { const r = document.querySelector('.jr-controls').getBoundingClientRect(); return r.left >= 0 && r.right <= innerWidth && r.bottom <= innerHeight; })()")
    check("mobile: no horizontal overflow; controls on screen", ov == 0 and ctrl, f"overflow {ov}px")
    check("mobile: evidence card starts collapsed (sky first)", page.evaluate("document.getElementById('journey').classList.contains('panel-closed')"))
    for sid, t, name in (("time", 12000, "2-time"), ("measure", 10000, "3-measure"), ("light", 15000, "4-light"), ("observer", 9000, "5-observer")):
        goto_scene(page, sid); wait_t(page, t)
        page.screenshot(path=str(out / f"mobile-{name}.png"))
    page.click("#jr-evidence"); page.wait_for_timeout(400)
    check("mobile: Evidence toggle opens the card", page.locator("#jr-panel").is_visible())
    page.screenshot(path=str(out / "mobile-5b-evidence.png"))
    page.wait_for_selector("#jr-end", state="visible", timeout=30000)
    page.click("#jr-evidence"); page.screenshot(path=str(out / "mobile-6-end.png"))
    page.click('[data-jr-action="return"]'); page.wait_for_timeout(400)
    check("mobile: return restores the scrollable observatory", not page.evaluate("document.body.classList.contains('journey-on')") and page.locator(".topbar").is_visible())
    ctx.close(); browser.close()


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--url", default="http://127.0.0.1:8001")
    ap.add_argument("--out", type=Path, default=Path("artifacts/journey"))
    args = ap.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    errors = []
    with sync_playwright() as pw:
        run_desktop(pw, args.url, args.out, errors)
        run_failure(pw, args.url, args.out, errors)
        run_reduced(pw, args.url, args.out, errors)
        run_mobile(pw, args.url, args.out, errors)
    check("no page errors in any run", not errors, "; ".join(errors[:5]))
    report = {"results": RESULTS, "errors": errors}
    (args.out / "verify_journey.json").write_text(json.dumps(report, indent=2))
    failed = [r for r in RESULTS if not r["ok"]]
    print(f"\n{len(RESULTS) - len(failed)}/{len(RESULTS)} checks passed")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
