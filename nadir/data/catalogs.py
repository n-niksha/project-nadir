"""Data access: external catalogs (Gaia DR3 hosted at IRSA, SIMBAD name resolution).

Catalog access is optional for the pipeline: every caller must tolerate a
``DataAccessError`` and continue without the cross-reference.
"""
from __future__ import annotations

import math

from .irsa import DataAccessError, cached_tap

# Tried in order; the CDS primary was answering 404 during development (2026-11),
# the CfA mirror serves the same database.
SIMBAD_TAP_MIRRORS = ("https://simbad.cds.unistra.fr/simbad/sim-tap", "https://simbad.harvard.edu/simbad/sim-tap")
GAIA_REF_EPOCH = 2016.0


def gaia_cone(ra: float, dec: float, radius_deg: float, g_limit: float = 17.0) -> tuple[list[dict], dict]:
    """Gaia DR3 sources brighter than ``g_limit`` in a cone (positions at epoch 2016.0)."""
    adql = (
        "SELECT source_id, ra, dec, pmra, pmdec, parallax, pmra_error, pmdec_error, "
        "phot_g_mean_mag, ruwe FROM gaia_dr3_source "
        f"WHERE CONTAINS(POINT('ICRS', ra, dec), CIRCLE('ICRS', {ra:.6f}, {dec:.6f}, {radius_deg:.5f})) = 1 "
        f"AND phot_g_mean_mag < {g_limit:.2f} ORDER BY phot_g_mean_mag"
    )
    rows, prov = cached_tap(adql, ttl=365 * 24 * 3600.0)
    clean = []
    for r in rows:
        def f(key):
            v = r.get(key)
            try:
                v = float(v)
            except (TypeError, ValueError):
                return None
            return None if math.isnan(v) else v
        clean.append({
            "source_id": str(r["source_id"]),
            "ra": float(r["ra"]), "dec": float(r["dec"]),
            "pmra": f("pmra"), "pmdec": f("pmdec"), "parallax": f("parallax"),
            "pmra_error": f("pmra_error"), "pmdec_error": f("pmdec_error"),
            "gmag": f("phot_g_mean_mag"), "ruwe": f("ruwe"),
        })
    prov = dict(prov, catalog="Gaia DR3 (gaia_dr3_source at IRSA)")
    return clean, prov


def resolve_name(name: str, epoch_year: float | None = None) -> dict:
    """Resolve an object name with SIMBAD.

    SIMBAD positions are ICRS at epoch J2000.  For high proper-motion objects the
    J2000 position can be arcminutes from where the object is in SPHEREx images
    (Barnard's Star has moved ~4.6' since 2000), so when ``epoch_year`` is given
    and a proper motion is known the position is propagated linearly to it.
    """
    import pyvo

    safe = name.replace("'", "''").strip()
    if not safe:
        raise ValueError("Empty object name")
    adql = (
        "SELECT TOP 1 b.main_id, b.ra, b.dec, b.pmra, b.pmdec, b.plx_value "
        f"FROM basic AS b JOIN ident AS i ON i.oidref = b.oid WHERE i.id = '{safe}'"
    )
    tab, errors = None, []
    for url in SIMBAD_TAP_MIRRORS:
        try:
            tab = pyvo.dal.TAPService(url).search(adql).to_table()
            break
        except Exception as exc:  # try the next mirror
            errors.append(f"{url}: {exc}")
    if tab is None:
        raise DataAccessError("SIMBAD query failed on all mirrors: " + " | ".join(errors))
    if len(tab) == 0:
        raise DataAccessError(f"SIMBAD does not know an object named {name!r}")
    row = tab[0]

    def val(col):
        v = row[col]
        try:
            v = float(v)
        except (TypeError, ValueError):
            return None
        return None if math.isnan(v) else v

    ra, dec = val("ra"), val("dec")
    if ra is None or dec is None:
        raise DataAccessError(f"SIMBAD has no coordinates for {name!r}")
    pmra, pmdec = val("pmra"), val("pmdec")
    out = {"name": str(row["main_id"]), "ra_j2000": ra, "dec_j2000": dec, "pmra_masyr": pmra,
           "pmdec_masyr": pmdec, "parallax_mas": val("plx_value"), "ra": ra, "dec": dec, "epoch": 2000.0}
    if epoch_year is not None and pmra is not None and pmdec is not None:
        dt = epoch_year - 2000.0
        out["dec"] = dec + pmdec * dt / 3.6e6
        out["ra"] = (ra + pmra * dt / 3.6e6 / math.cos(math.radians(dec))) % 360.0
        out["epoch"] = epoch_year
    return out
