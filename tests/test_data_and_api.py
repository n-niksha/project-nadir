"""Data-access, FITS parsing, pairing, pipeline and HTTP API tests.

Everything is offline: archive responses are replaced by synthetic FITS."""
import io
import json

import numpy as np
import pytest
from fastapi.testclient import TestClient

from nadir import config, pipeline
from nadir.data import irsa
from nadir.science import pairing, synthetic
from nadir.science.observation import ObservationError, load_observation

RA0, DEC0, N = 150.0, 20.0, 101
SIZE = round(N * 6.15 / 3600.0, 4)


def stars(seed=1, n=50):
    return synthetic.random_field(n, RA0, DEC0, SIZE * 0.45, np.random.default_rng(seed))


def fits_bytes(obs, extra_hdu=True):
    hdul = synthetic.to_fits(obs)
    if extra_hdu:  # stand-in for the bulky EPSF table that must not be cached
        from astropy.io import fits
        hdul.append(fits.ImageHDU(np.zeros((300, 300)), name="EPSF"))
    buf = io.BytesIO()
    hdul.writeto(buf)
    return buf.getvalue()


def record(name="level2_2025W35_1A_0146_1D2_spx_l2b", mjd=60912.7, rel="dr1"):
    return irsa.ArchiveRecord(f"ibe/data/spherex/{rel}/level2/x/{name}.fits", "D2", mjd, mjd + 0.001,
                              "ivo://irsa.ipac/spherex?x", "7.0.5", "md5:abc")


# ---------------- data access ----------------

def test_cutout_url_and_release_parsing():
    r = record()
    url = irsa.cutout_url(r, 269.4463, 4.7682, 0.2)
    assert url.startswith("https://irsa.ipac.caltech.edu/ibe/data/spherex/dr1/")
    assert url.endswith("?center=269.446300,4.768200d&size=0.2000")
    assert r.release == "dr1" and record(rel="qr3").release == "qr3"


def test_fetch_caches_strips_epsf_and_records_provenance(monkeypatch):
    obs = synthetic.render_observation(stars(), n=N)
    payload, calls = fits_bytes(obs), []
    monkeypatch.setattr(irsa, "_http_get", lambda url: calls.append(url) or payload)
    path, prov = irsa.fetch_cutout(record(), RA0, DEC0, SIZE)
    assert path.exists() and config.CACHE_DIR in path.parents
    assert "EPSF" in prov["hdus_received"] and "EPSF" not in prov["hdus_cached"]
    assert path.stat().st_size < len(payload) / 2
    import hashlib
    assert prov["response_sha256"] == hashlib.sha256(payload).hexdigest()
    assert prov["source_url"] == calls[0] and prov["release"] == "dr1"
    assert prov["archive_record"]["archive_checksum"] == "md5:abc" and prov["retrieved_utc"]
    assert json.loads(path.with_suffix(".json").read_text()) == prov
    # second call: served from cache, no new request
    path2, prov2 = irsa.fetch_cutout(record(), RA0, DEC0, SIZE)
    assert path2 == path and prov2 == prov and len(calls) == 1
    # pixels are unmodified by the cache round trip
    loaded = load_observation(path, (RA0, DEC0), prov)
    np.testing.assert_allclose(loaded.image, obs.image.astype(np.float32), rtol=1e-6)


def test_failed_downloads_are_reported_per_item(monkeypatch):
    good = fits_bytes(synthetic.render_observation(stars(), n=N))

    def fake(url):
        if "bad" in url:
            raise irsa.DataAccessError("HTTP 500")
        return good
    monkeypatch.setattr(irsa, "_http_get", fake)
    out = irsa.fetch_cutouts([record("good_a"), record("bad_b"), record("good_c")], RA0, DEC0, SIZE, workers=2)
    assert [o["error"] is None for o in out] == [True, False, True]
    assert out[1]["path"] is None and "HTTP 500" in out[1]["error"]


def test_non_fits_response_is_rejected(monkeypatch):
    class Resp:
        status_code, content = 200, b"<html>Cutout error: position outside image</html>"
    import requests
    monkeypatch.setattr(requests, "get", lambda *a, **k: Resp())
    monkeypatch.setattr(irsa.time, "sleep", lambda s: None)
    with pytest.raises(irsa.DataAccessError, match="non-FITS"):
        irsa.fetch_cutout(record(), RA0, DEC0, SIZE)
    assert not list((config.CACHE_DIR / "cutouts").glob("*")) if (config.CACHE_DIR / "cutouts").exists() else True


def test_find_observations_parses_rows_and_skips_incomplete(monkeypatch):
    rows = [{"uri": "ibe/data/spherex/qr3/level2/a.fits", "time_bounds_lower": 61270.8, "time_bounds_upper": 61270.9,
             "obs_publisher_did": "ivo://x", "provenance_version": "7.0.5", "contentchecksum": "md5:1"},
            {"uri": "ibe/data/spherex/dr1/level2/b.fits", "time_bounds_lower": None, "time_bounds_upper": None}]
    seen = {}
    monkeypatch.setattr(irsa, "cached_tap", lambda adql, **k: (seen.setdefault("q", adql) and rows, {"adql": adql}))
    recs, prov = irsa.find_observations(10.0, -5.0, "D3")
    assert len(recs) == 1 and recs[0].release == "qr3" and recs[0].band == "D3"
    assert "SPHEREx-D3" in seen["q"] and "POINT('ICRS', 10.000000, -5.000000)" in seen["q"]
    with pytest.raises(ValueError):
        irsa.find_observations(400.0, 0.0)
    with pytest.raises(ValueError):
        irsa.find_observations(10.0, 0.0, "D9")


def test_tap_cache_is_reused(monkeypatch):
    n = []
    monkeypatch.setattr(irsa, "_tap_sync", lambda adql: n.append(1) or [{"a": 1}])
    r1, p1 = irsa.cached_tap("SELECT 1")
    r2, p2 = irsa.cached_tap("SELECT 1")
    assert r1 == r2 == [{"a": 1}] and len(n) == 1 and p1["cached"] is False and p2["cached"] is True
    irsa.cached_tap("SELECT 1", refresh=True)
    assert len(n) == 2


# ---------------- FITS parsing ----------------

def test_loader_reads_metadata_and_tolerates_missing_optional_parts(tmp_path):
    obs = synthetic.render_observation(stars(), n=N, mjd=61234.5, saturation=None)
    p = tmp_path / "a.fits"
    synthetic.to_fits(obs).writeto(p)
    got = load_observation(p, (RA0, DEC0))
    assert got.mjd == 61234.5 and got.detector == 2 and got.image.shape == (N, N)
    assert got.flag_bits["OVERFLOW"] == 1 and got.flag_bits["HALO"] == 28
    assert got.wavelength_um is None and any("WCS-WAVE" in i for i in got.issues)
    assert any("ZODI" in i for i in got.issues)
    assert abs(got.detector_pa_deg) < 1e-6 or abs(got.detector_pa_deg - 360) < 1e-6
    np.testing.assert_allclose(got.wcs.all_pix2world([[50, 50]], 0), [[RA0, DEC0]], atol=1e-9)


def test_loader_flags_and_bad_mask(tmp_path):
    obs = synthetic.render_observation(stars(), n=N)
    obs.flags[10, 10] |= 1 << 6   # NONFUNC -> unusable
    obs.flags[20, 20] |= 1 << 21  # SOURCE -> informational only
    obs.image[30, 30] = np.nan
    p = tmp_path / "a.fits"
    synthetic.to_fits(obs).writeto(p)
    got = load_observation(p)
    assert got.bad_mask[10, 10] and got.bad_mask[30, 30] and not got.bad_mask[20, 20]
    assert got.flag_names(int(got.flags[10, 10])) == ["NONFUNC"]


def test_loader_rejects_incomplete_or_corrupt_files(tmp_path):
    hdul = synthetic.to_fits(synthetic.render_observation(stars(), n=N))
    del hdul["VARIANCE"]
    p = tmp_path / "novar.fits"
    hdul.writeto(p)
    with pytest.raises(ObservationError, match="VARIANCE"):
        load_observation(p)
    junk = tmp_path / "junk.fits"
    junk.write_bytes(b"not a fits file")
    with pytest.raises(ObservationError):
        load_observation(junk)


def test_loader_missing_timestamp_is_an_issue_not_a_crash(tmp_path):
    hdul = synthetic.to_fits(synthetic.render_observation(stars(), n=N))
    del hdul["IMAGE"].header["MJD-AVG"]
    p = tmp_path / "nomjd.fits"
    hdul.writeto(p)
    got = load_observation(p)
    assert got.mjd is None and any("MJD-AVG" in i for i in got.issues)
    assert pairing.is_usable(got, N * N) == (False, "no timestamp")


# ---------------- pairing ----------------

def test_pair_ranking_prefers_long_baseline_then_wavelength():
    st = stars()
    spec = [(61000, 1.50), (61001, 1.30), (61180, 1.51), (61365, 1.505), (61366, 1.31), (61367, 1.70)]
    obs = [synthetic.render_observation(st, n=31, mjd=float(m), wavelength_um=w, seed=i) for i, (m, w) in enumerate(spec)]
    pairs = pairing.rank_pairs(obs, [True] * 6, max_rel_dlambda=0.02)
    assert (pairs[0]["a"], pairs[0]["b"]) == (0, 3)
    assert {(p["a"], p["b"]) for p in pairs} == {(0, 3), (1, 4), (0, 2), (2, 3)}
    assert all(p["rel_dlambda"] <= 0.02 and p["dt_days"] >= 30 for p in pairs)
    assert pairing.rank_pairs(obs, [True, False, False, False, False, True]) == []


def test_truncated_cutout_is_unusable():
    obs = synthetic.render_observation(stars(), n=40)
    assert pairing.is_usable(obs, N * N) == (False, "cutout truncated by detector edge")


# ---------------- pipeline + API on synthetic archive ----------------

@pytest.fixture
def fake_archive(monkeypatch):
    """An 'archive' with 4 epochs; one star moves 10.4"/yr north; one download fails."""
    st = stars(seed=3, n=60)
    c = np.hypot((st[:, 0] - RA0) * np.cos(np.radians(DEC0)), st[:, 1] - DEC0)
    st = st[c > 30 / 3600]                       # keep the centre clear ...
    st = np.vstack([[RA0, DEC0, 4000.0], st])    # ... for the moving target
    epochs = [(61000.0, 1.50), (61002.0, 1.30), (61365.25, 1.505), (61366.0, 1.31)]
    blobs = {}
    recs = []
    for k, (mjd, wl) in enumerate(epochs):
        s = st.copy()
        s[0, 1] += 10.4 * (mjd - 61000.0) / 365.25 / 3600.0
        obs = synthetic.render_observation(s, n=N, mjd=mjd, wavelength_um=wl, seed=k, obs_id=f"E{k}",
                                           wcs_error_arcsec=(0.5 * k, -0.3 * k))
        blobs[f"epoch{k}"] = (fits_bytes(obs), wl)
        recs.append(record(f"epoch{k}", mjd))
    recs.append(record("broken", 61400.0))

    def http(url):
        for key, (blob, _) in blobs.items():
            if key in url:
                return blob
        raise irsa.DataAccessError("HTTP 503 from archive")
    monkeypatch.setattr(irsa, "_http_get", http)
    monkeypatch.setattr(irsa, "find_observations", lambda ra, dec, band, refresh=False: (recs, {"adql": "fake"}))

    real_load = pipeline.load_observation

    def load(path, ref, prov):
        o = real_load(path, ref, prov)
        key = next(k for k in blobs if k in prov["source_url"])
        o.wavelength_um, o.bandwidth_um = blobs[key][1], blobs[key][1] / 41
        return o
    monkeypatch.setattr(pipeline, "load_observation", load)
    monkeypatch.setattr(pipeline.catalogs, "gaia_cone",
                        lambda *a, **k: (_ for _ in ()).throw(irsa.DataAccessError("Gaia offline")))
    return st


def test_pipeline_end_to_end_on_synthetic_archive(fake_archive):
    s = pipeline.search(RA0, DEC0, SIZE, "D2")
    summ = pipeline.session_summary(s)
    assert summ["n_in_archive"] == 5 and summ["n_loaded"] == 4 and len(summ["failures"]) == 1
    assert "503" in summ["failures"][0]["error"]
    best = summ["suggested_pairs"][0]
    assert (best["a"], best["b"]) == (0, 2)
    r = pipeline.analyze(s, 2, 0)  # order is normalised to time order
    assert r["a"]["index"] == 0 and r["b"]["index"] == 2 and r["ok"]
    assert r["gaia"] == {"available": False, "error": "Gaia offline"}  # optional catalog failure is survivable
    tgt = next(x for x in r["sources"] if x["id"] == r["target_id"])
    assert tgt["status"] == "candidate_motion"
    assert abs(tgt["d_north_arcsec"] - 10.4) < 0.4 and abs(tgt["d_east_arcsec"]) < 0.4
    assert r["verdict"]["candidates"] == [tgt["id"]]
    assert "motion candidate" in r["verdict"]["target"] and "No Gaia DR3 counterpart" in r["verdict"]["target"]
    # the injected per-epoch WCS error (1.0", -0.6") was measured by the registration
    assert abs(r["registration"]["shift_east_arcsec"] + 1.0) < 0.1 and abs(r["registration"]["shift_north_arcsec"] - 0.6) < 0.1
    assert abs(r["xcorr_check"]["shift_north_arcsec"] - 0.6) < 0.6
    json.dumps({k: v for k, v in r.items() if k != "arrays"}, allow_nan=False)  # strictly JSON-safe
    d = r["arrays"]["diff"]
    assert np.isfinite(d).mean() > 0.9
    tr = pipeline.track(s, 0, tgt["ra"], tgt["dec"])
    assert len(tr["points"]) == 4 and abs(tr["fit"]["north"]["pm_arcsec_per_yr"] - 10.4) < 0.4
    with pytest.raises(ValueError):
        pipeline.analyze(s, 0, 0)


def test_http_api(fake_archive):
    from nadir.api.server import app
    c = TestClient(app)
    assert c.get("/api/health").json()["status"] == "ok"
    q = {"ra": RA0, "dec": DEC0, "size": SIZE, "band": "D2"}
    s = c.get("/api/search", params=q).json()
    assert s["n_usable"] == 4 and len(s["observations"]) == 4
    r = c.get("/api/analyze", params={**q, "a": 0, "b": 2})
    assert r.status_code == 200
    body = r.json()
    for k in ("a", "b", "b_wcs", "diff"):
        assert body["images"][k].startswith("data:image/png;base64,")
    assert body["verdict"]["candidates"] and "arrays" not in body
    assert c.get("/api/analyze", params={**q, "a": 0, "b": 0}).status_code == 400
    assert c.get("/api/analyze", params={**q, "a": 0, "b": 99}).status_code == 400
    assert c.get("/api/search", params={**q, "ra": 400}).status_code == 422
    assert c.get("/api/search", params={**q, "band": "X1"}).status_code == 422
    p = c.get("/api/provenance", params={**q, "index": 0}).json()
    assert "response_sha256" in p["provenance"] and p["provenance"]["source_url"].startswith("https://irsa")
    t = c.get("/api/track", params={**q, "ref": 0, "sra": RA0, "sdec": DEC0}).json()
    assert len(t["points"]) == 4
    page = c.get("/")
    assert page.status_code == 200 and "Project NADIR" in page.text
    assert c.get("/static/app.js").status_code == 200


def test_api_reports_archive_outage(monkeypatch):
    from nadir.api.server import app
    monkeypatch.setattr(irsa, "find_observations",
                        lambda *a, **k: (_ for _ in ()).throw(irsa.DataAccessError("TAP timeout")))
    r = TestClient(app).get("/api/search", params={"ra": 1.0, "dec": 2.0})
    assert r.status_code == 502 and "TAP timeout" in r.json()["detail"]


def test_empty_archive_result_is_handled(monkeypatch):
    monkeypatch.setattr(irsa, "find_observations", lambda *a, **k: ([], {"adql": "fake"}))
    summ = pipeline.session_summary(pipeline.search(1.0, 2.0))
    assert summ["n_loaded"] == 0 and summ["suggested_pairs"] == [] and "Fewer than two" in summ["pair_note"]


def test_ml_feature_interface(fake_archive):
    from nadir.ml import features
    s = pipeline.search(RA0, DEC0, SIZE, "D2")
    r = pipeline.analyze(s, 0, 2)
    f = features.source_features(next(x for x in r["sources"] if x["id"] == r["target_id"]), r["dt_days"])
    assert set(features.FEATURES) <= set(f) and f["schema"] == features.FEATURE_SCHEMA_VERSION
    assert abs(f["rate_arcsec_per_yr"] - 10.4) < 0.5 and f["position_angle_deg"] < 5 or f["position_angle_deg"] > 355
    assert f["has_gaia"] is False and f["gaia_residual_sigma"] is None
