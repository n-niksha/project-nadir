"""Data access: external catalogs (Gaia DR3 hosted at IRSA, SIMBAD name resolution).

Catalog access is optional for the pipeline: every caller must tolerate a
``DataAccessError`` and continue without the cross-reference.
"""
from __future__ import annotations

import math

from .irsa import DataAccessError, cached_tap
from .http import TimeoutSession

# Tried in order; the CDS primary was answering 404 during development (2026-11),
# the CfA mirror serves the same database.
SIMBAD_TAP_MIRRORS = ("https://simbad.cds.unistra.fr/simbad/sim-tap", "https://simbad.harvard.edu/simbad/sim-tap")
GAIA_REF_EPOCH = 2016.0


def gaia_cone(ra: float, dec: float, radius_deg: float, g_limit: float = 17.0) -> tuple[list[dict], dict]:
    """Gaia DR3 sources brighter than ``g_limit`` in a cone (positions at epoch 2016.0)."""
    adql = (
        "SELECT source_id, ra, dec, pmra, pmdec, parallax, pmra_error, pmdec_error, "
        "phot_g_mean_mag, ruwe, bp_rp, teff_gspphot, phot_variable_flag, non_single_star, "
        "classprob_dsc_combmod_star, classprob_dsc_combmod_galaxy, classprob_dsc_combmod_quasar "
        "FROM gaia_dr3_source "
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
            "gmag": f("phot_g_mean_mag"), "ruwe": f("ruwe"), "bp_rp": f("bp_rp"),
            "teff_gspphot": f("teff_gspphot"),
            "variable": str(r.get("phot_variable_flag") or "") == "VARIABLE",
            "non_single_star": bool(f("non_single_star") or 0),
            "p_star": f("classprob_dsc_combmod_star"), "p_galaxy": f("classprob_dsc_combmod_galaxy"),
            "p_quasar": f("classprob_dsc_combmod_quasar"),
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
            tab = pyvo.dal.TAPService(url, session=TimeoutSession()).search(adql).to_table()
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


def _num(v):
    try:
        v = float(v)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(v) else v


TWOMASS_ZP_JY = {"J": 1594.0, "H": 1024.0, "K": 666.7}  # Cohen et al. 2003
TWOMASS_WL_UM = {"J": 1.235, "H": 1.662, "K": 2.159}
WISE_ZP_JY = {"W1": 309.540, "W2": 171.787}  # Jarrett et al. 2011 (Vega, no colour correction)
WISE_WL_UM = {"W1": 3.3526, "W2": 4.6028}


def twomass_cone(ra: float, dec: float, radius_deg: float, kmax: float = 15.5) -> tuple[list[dict], dict]:
    """2MASS Point Source Catalog (epoch = per-source observation date, 1997-2001)."""
    adql = ("SELECT designation, ra, dec, j_m, j_cmsig, h_m, h_cmsig, k_m, k_cmsig, ph_qual, cc_flg, jdate "
            "FROM fp_psc WHERE CONTAINS(POINT('ICRS', ra, dec), "
            f"CIRCLE('ICRS', {ra:.6f}, {dec:.6f}, {radius_deg:.5f})) = 1 AND k_m < {kmax:.1f}")
    rows, prov = cached_tap(adql, ttl=365 * 24 * 3600.0)
    out = []
    for r in rows:
        jd = _num(r.get("jdate"))
        out.append({"id": str(r["designation"]), "ra": float(r["ra"]), "dec": float(r["dec"]),
                    "mjd": jd - 2400000.5 if jd else None, "ph_qual": str(r.get("ph_qual") or ""),
                    "cc_flg": str(r.get("cc_flg") or ""),
                    "mags": {b: _num(r.get(f"{b.lower()}_m")) for b in "JHK"},
                    "mag_errs": {b: _num(r.get(f"{b.lower()}_cmsig")) for b in "JHK"}})
    return out, dict(prov, catalog="2MASS Point Source Catalog (fp_psc at IRSA)")


def allwise_cone(ra: float, dec: float, radius_deg: float, w1max: float = 15.0) -> tuple[list[dict], dict]:
    """AllWISE source catalog (positions at the mean W1 epoch, ~2010)."""
    adql = ("SELECT designation, ra, dec, w1mpro, w1sigmpro, w2mpro, w2sigmpro, ph_qual, cc_flags, ext_flg, "
            "w1mjdmean, pmra, sigpmra, pmdec, sigpmdec FROM allwise_p3as_psd WHERE CONTAINS(POINT('ICRS', ra, dec), "
            f"CIRCLE('ICRS', {ra:.6f}, {dec:.6f}, {radius_deg:.5f})) = 1 AND w1mpro < {w1max:.1f}")
    rows, prov = cached_tap(adql, ttl=365 * 24 * 3600.0)
    out = []
    for r in rows:
        out.append({"id": str(r["designation"]), "ra": float(r["ra"]), "dec": float(r["dec"]),
                    "mjd": _num(r.get("w1mjdmean")), "ph_qual": str(r.get("ph_qual") or ""),
                    "cc_flags": str(r.get("cc_flags") or ""), "extended": bool(_num(r.get("ext_flg")) or 0),
                    "mags": {"W1": _num(r.get("w1mpro")), "W2": _num(r.get("w2mpro"))},
                    "mag_errs": {"W1": _num(r.get("w1sigmpro")), "W2": _num(r.get("w2sigmpro"))}})
    return out, dict(prov, catalog="AllWISE Source Catalog (allwise_p3as_psd at IRSA)")


def simbad_region(ra: float, dec: float, radius_deg: float) -> tuple[list[dict], dict]:
    """SIMBAD objects (identifier, object type, spectral type) in a cone."""
    import pyvo
    adql = ("SELECT main_id, ra, dec, otype, sp_type, pmra, pmdec FROM basic WHERE "
            f"CONTAINS(POINT('ICRS', ra, dec), CIRCLE('ICRS', {ra:.6f}, {dec:.6f}, {radius_deg:.5f})) = 1")

    def fetch():
        errors = []
        for url in SIMBAD_TAP_MIRRORS:
            try:
                tab = pyvo.dal.TAPService(url, session=TimeoutSession()).search(adql).to_table()
                return [{c: (row[c].item() if hasattr(row[c], "item") else row[c]) for c in tab.colnames} for row in tab]
            except Exception as exc:
                errors.append(f"{url}: {exc}")
        raise DataAccessError("SIMBAD region query failed on all mirrors: " + " | ".join(errors))

    from .irsa import cached_query
    rows, prov = cached_query("SIMBAD " + adql, fetch, "SIMBAD TAP", 30 * 24 * 3600.0)
    out = []
    for r in rows:
        if _num(r.get("ra")) is None or _num(r.get("dec")) is None:
            continue
        out.append({"id": str(r["main_id"]), "ra": float(r["ra"]), "dec": float(r["dec"]),
                    "otype": str(r.get("otype") or ""), "sp_type": str(r.get("sp_type") or ""),
                    "pmra": _num(r.get("pmra")), "pmdec": _num(r.get("pmdec"))})
    return out, dict(prov, catalog="SIMBAD (basic table; CDS primary, CfA mirror)")


# Coarse grouping of SIMBAD object types for the UI filter.  SIMBAD reports the
# type; NADIR only buckets it.
OTYPE_GROUPS = {
    "star": {"*", "PM*", "HPM*", "**", "SB*", "EB*", "low-mass*", "BD*", "WD*", "RG*", "HB*", "AB*", "MS*", "C*",
             "S*", "LM*", "RS*", "OH*", "pr*", "Y*O", "TT*", "Em*", "Be*", "HS*", "s*b", "s*r", "s*y", "BS*",
             "SN*", "Ce*", "RR*", "dS*", "gD*", "Mi*", "LP*", "BY*", "Ro*", "El*", "Er*", "Pu*", "Ir*", "V*", "Fl*"},
    "variable": {"V*", "Ir*", "Er*", "Fl*", "Ro*", "BY*", "RS*", "Pu*", "RR*", "Ce*", "dS*", "Mi*", "LP*", "EB*",
                 "El*", "SB*", "gD*", "TT*", "Or*"},
    "galaxy": {"G", "GiG", "GiC", "GiP", "BiC", "IG", "PaG", "SBG", "SyG", "Sy1", "Sy2", "AGN", "LIN", "EmG", "H2G",
               "bCG", "LSB", "rG", "GrG", "ClG", "CGG"},
    "quasar": {"QSO", "Bla", "BLL", "Q?"},
    "nebula": {"ISM", "HII", "PN", "RNe", "MoC", "DNe", "Cld", "glb", "cor", "SFR", "bub", "SNR"},
}


def otype_groups(otype: str) -> list[str]:
    o = otype.strip()
    return [g for g, members in OTYPE_GROUPS.items() if o in members] or ["other"]
