"""Parse a SPHEREx Level-2 spectral-image cutout into an in-memory ``Observation``.

Handles: science image, per-pixel flags, variance, zodiacal model, spatial WCS
(TAN-SIP), the spectral WCS (``WAVE-TAB`` lookup, alternate key ``W``),
timestamps and detector metadata.  Missing optional metadata degrades to
``None`` with a note in ``Observation.issues`` rather than raising.
"""
from __future__ import annotations

import logging
import warnings
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
from astropy.io import fits
from astropy.wcs import WCS, FITSFixedWarning

# Flag bit numbers as documented in the FLAGS header (MSKNnnnn cards) of
# DR1/QR3 products.  Used only as a fallback if a header lacks the cards.
DEFAULT_FLAG_BITS = {
    "TRANSIENT": 0, "OVERFLOW": 1, "SUR_ERROR": 2, "NONFUNC": 6, "DICHROIC": 7,
    "MISSING_DATA": 9, "HOT": 10, "COLD": 11, "FULLSAMPLE": 12, "PHANMISS": 14,
    "NONLINEAR": 15, "PERSIST": 17, "OUTLIER": 19, "CROSSTALK": 20, "SOURCE": 21,
    "GHOST": 22, "GHOST_FPA": 23, "GHOST_EXT": 24, "STREAK": 25, "BLOOM": 26,
    "SNOWBALL": 27, "HALO": 28,
}
# Pixels with these flags carry no usable flux measurement and are excluded from fits.
HARD_BAD = ("OVERFLOW", "NONFUNC", "MISSING_DATA", "HOT")
# These flags do not invalidate a pixel outright but mark possible contamination
# or artifacts; they are reported per source.
WARN_FLAGS = ("TRANSIENT", "SUR_ERROR", "COLD", "NONLINEAR", "PERSIST", "OUTLIER", "CROSSTALK",
              "GHOST", "GHOST_FPA", "GHOST_EXT", "STREAK", "BLOOM", "SNOWBALL", "HALO", "DICHROIC")


class ObservationError(ValueError):
    """The file cannot be used as a SPHEREx observation."""


@dataclass
class Observation:
    obs_id: str
    image: np.ndarray  # MJy/sr, float
    flags: np.ndarray  # int bitmask
    variance: np.ndarray  # (MJy/sr)^2
    wcs: WCS
    flag_bits: dict[str, int]
    mjd: float | None  # exposure midpoint, UTC
    date_iso: str | None
    detector: int | None
    exptime_s: float | None
    psf_fwhm_arcsec: float | None
    wavelength_um: float | None  # at the reference (target) position
    bandwidth_um: float | None
    zodi: np.ndarray | None = None
    release: str | None = None
    pipeline_version: str | None = None
    path: str | None = None
    provenance: dict | None = None
    issues: list[str] = field(default_factory=list)
    epsf: object | None = None  # psf.EPSFGrid when the archive ePSF table is available
    wcs_det: WCS | None = None  # cutout pixel -> detector pixel (alternate WCS 'A')
    pixel_sr: float | None = None  # median pixel solid angle (OMEGA_MEDIAN), steradian

    def bitmask(self, names) -> int:
        m = 0
        for n in names:
            if n in self.flag_bits:
                m |= 1 << self.flag_bits[n]
        return m

    @property
    def bad_mask(self) -> np.ndarray:
        """True where a pixel must not be used (non-finite or hard-bad flag)."""
        bad = ~np.isfinite(self.image) | ~np.isfinite(self.variance) | (self.variance <= 0)
        return bad | ((self.flags & self.bitmask(HARD_BAD)) != 0)

    @property
    def saturated_mask(self) -> np.ndarray:
        return (self.flags & self.bitmask(("OVERFLOW",))) != 0

    def flag_names(self, value: int) -> list[str]:
        return [n for n, b in sorted(self.flag_bits.items(), key=lambda kv: kv[1]) if value >> b & 1]

    @property
    def detector_pa_deg(self) -> float:
        """Position angle (deg E of N) of the detector +y axis on the sky."""
        m = self.wcs.celestial.pixel_scale_matrix  # d(lon, lat) / d(x, y)
        return float(np.degrees(np.arctan2(m[0, 1], m[1, 1])) % 360.0)

    def summary(self) -> dict:
        return {
            "obs_id": self.obs_id, "mjd": self.mjd, "date": self.date_iso, "detector": self.detector,
            "wavelength_um": self.wavelength_um, "bandwidth_um": self.bandwidth_um,
            "exptime_s": self.exptime_s, "psf_fwhm_arcsec": self.psf_fwhm_arcsec,
            "detector_pa_deg": self.detector_pa_deg,
            "release": self.release, "pipeline_version": self.pipeline_version,
            "shape": list(self.image.shape), "bad_fraction": float(self.bad_mask.mean()),
            "issues": list(self.issues),
            "source_url": (self.provenance or {}).get("source_url"),
            "retrieved_utc": (self.provenance or {}).get("retrieved_utc"),
        }


def _flag_bits_from_header(header) -> dict[str, int]:
    bits = {}
    for key in header.get("MSKN*", {}):
        try:
            bits[str(header[key]).strip()] = int(key[4:])
        except ValueError:
            continue
    return bits


def wavelength_at(hdul, x: float, y: float) -> tuple[float, float]:
    """Central wavelength and bandwidth (um) at 0-based pixel (x, y) via the 'W' WCS."""
    wlog = logging.getLogger("astropy")
    level = wlog.level
    wlog.setLevel(logging.WARNING)  # silence the benign SIP INFO message
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            swcs = WCS(hdul["IMAGE"].header, fobj=hdul, key="W")
            swcs.sip = None
            wl, bw = swcs.wcs_pix2world([[x, y]], 0)[0]
    finally:
        wlog.setLevel(level)
    return float(wl), float(bw)


def load_observation(path, ref_radec: tuple[float, float] | None = None,
                     provenance: dict | None = None) -> Observation:
    """Load a cutout.  ``ref_radec`` is where the wavelength is evaluated
    (default: cutout centre) -- SPHEREx wavelength varies across the detector."""
    path = Path(path)
    issues: list[str] = []
    try:
        hdul = fits.open(path, memmap=False)
    except OSError as exc:
        raise ObservationError(f"Cannot open {path}: {exc}") from exc
    with hdul:
        names = [h.name for h in hdul]
        for req in ("IMAGE", "FLAGS", "VARIANCE"):
            if req not in names:
                raise ObservationError(f"{path.name}: missing required HDU {req}")
        hdr = hdul["IMAGE"].header
        image = np.asarray(hdul["IMAGE"].data, dtype=np.float64)
        flags = np.asarray(hdul["FLAGS"].data, dtype=np.int64)
        variance = np.asarray(hdul["VARIANCE"].data, dtype=np.float64)
        if image.ndim != 2 or image.shape != flags.shape or image.shape != variance.shape:
            raise ObservationError(f"{path.name}: inconsistent HDU shapes")
        zodi = np.asarray(hdul["ZODI"].data, dtype=np.float64) if "ZODI" in names else None
        if zodi is None:
            issues.append("ZODI HDU missing")

        with warnings.catch_warnings():
            warnings.simplefilter("ignore", FITSFixedWarning)
            wcs = WCS(hdr)
        if not wcs.has_celestial:
            raise ObservationError(f"{path.name}: no celestial WCS")

        flag_bits = _flag_bits_from_header(hdul["FLAGS"].header)
        if not flag_bits:
            flag_bits = dict(DEFAULT_FLAG_BITS)
            issues.append("flag definitions missing from header; using documented defaults")

        ny, nx = image.shape
        if ref_radec is not None:
            x, y = (float(v) for v in wcs.all_world2pix([list(ref_radec)], 0)[0])
        else:
            x, y = (nx - 1) / 2.0, (ny - 1) / 2.0
        wl = bw = None
        if "WCS-WAVE" in names:
            try:
                wl, bw = wavelength_at(hdul, min(max(x, 0), nx - 1), min(max(y, 0), ny - 1))
                if not (np.isfinite(wl) and 0.5 < wl < 6.0):
                    issues.append(f"implausible wavelength {wl}")
                    wl = bw = None
            except Exception as exc:  # spectral WCS is optional metadata
                issues.append(f"spectral WCS unreadable: {exc}")
        else:
            issues.append("WCS-WAVE HDU missing; wavelength unknown")

        def get(key, cast):
            v = hdr.get(key)
            if v is None:
                issues.append(f"header keyword {key} missing")
                return None
            try:
                return cast(v)
            except (TypeError, ValueError):
                issues.append(f"header keyword {key} unparsable")
                return None

        mjd = get("MJD-AVG", float)
        if mjd is None and hdr.get("MJD-OBS") is not None:
            mjd = float(hdr["MJD-OBS"])
            issues.append("using MJD-OBS (exposure start) as timestamp")
        if hdr.get("L2DQAFLG", "Pass") != "Pass":
            issues.append(f"L2 DQA flag = {hdr.get('L2DQAFLG')}")
        if hdr.get("FINAST", 0) != 0:
            issues.append("fine astrometric solution flagged not good (FINAST != 0)")

        epsf = wcs_det = None
        if "EPSF" in names:
            try:
                from .psf import EPSFGrid
                epsf = EPSFGrid.from_hdu(hdul["EPSF"])
                with warnings.catch_warnings():
                    warnings.simplefilter("ignore")
                    wcs_det = WCS(hdr, key="A")
            except Exception as exc:  # optional
                issues.append(f"EPSF unreadable: {exc}")
                epsf = wcs_det = None
        omega = hdr.get("OMEGA_MEDIAN")
        pixel_sr = float(omega) * (np.pi / 180 / 3600) ** 2 if omega else None

        return Observation(
            epsf=epsf, wcs_det=wcs_det, pixel_sr=pixel_sr,
            obs_id=str(hdr.get("OBSID", path.stem)) + (f"D{hdr['DETECTOR']}" if "DETECTOR" in hdr else ""),
            image=image, flags=flags, variance=variance, wcs=wcs, flag_bits=flag_bits,
            mjd=mjd, date_iso=hdr.get("DATE-AVG") or hdr.get("DATE-OBS"),
            detector=get("DETECTOR", int), exptime_s=get("XPOSURE", float),
            psf_fwhm_arcsec=get("PSF_FWHM", float), wavelength_um=wl, bandwidth_um=bw, zodi=zodi,
            release=(provenance or {}).get("release"),
            pipeline_version=hdul[0].header.get("VERSION"),
            path=str(path), provenance=provenance, issues=issues,
        )
