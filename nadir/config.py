"""Central configuration: paths, service endpoints and tunable defaults."""
from __future__ import annotations

import os
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
CACHE_DIR = Path(os.environ.get("NADIR_CACHE_DIR", REPO_ROOT / "data" / "cache"))

IRSA_BASE = "https://irsa.ipac.caltech.edu/"
IRSA_TAP = IRSA_BASE + "TAP"
IRSA_SIA = IRSA_BASE + "SIA"
SIA_COLLECTIONS = ("spherex_dr1", "spherex_qr3")
SPHEREX_HIPS = IRSA_BASE + "data/hips/SPHEREx/spherex_allsky_cube"

# SPHEREx facts (verified against DR1/QR3 headers and the spherex.plane table).
PIXEL_SCALE_ARCSEC = 6.15
BANDS = {f"D{i}": f"SPHEREx-D{i}" for i in range(1, 7)}

# Defaults
DEFAULT_CUTOUT_SIZE_DEG = 0.2
DEFAULT_BAND = "D2"
DOWNLOAD_WORKERS = 6  # IRSA returns 503 'Server busy' under heavier parallel load
HTTP_TIMEOUT_S = 120
HTTP_RETRIES = 3
HTTP_RETRIES_BUSY = 6  # attempts when IRSA says 'busy' (backoff 2, 4, 8, 16, 30 s)
TAP_CACHE_TTL_S = 7 * 24 * 3600  # archive ingests weekly

# Curated targets.  Positions are SIMBAD (ICRS, J2000) propagated to epoch
# 2026.0 with SIMBAD proper motions (2026-11-13); the field is 0.2 deg so the
# star is inside it at every SPHEREx epoch.  "role" says why each is here.
PRESETS = [
    {"id": "barnard", "name": "Barnard's Star", "ra": 269.44627, "dec": 4.76820, "band": "D2", "size": 0.2,
     "role": "Flagship demo: the fastest-moving star in the sky (10.4\"/yr). Saturated in SPHEREx, so its "
             "measurement is systematics-limited and labelled as such.", "demo": True},
    {"id": "teegarden", "name": "Teegarden's Star", "ra": 43.27960, "dec": 16.85380, "band": "D2", "size": 0.2,
     "role": "Faint red dwarf, 5.1\"/yr. Independent validation target (not used for calibration)."},
    {"id": "vanmaanen", "name": "van Maanen's Star (Wolf 28)", "ra": 12.30018, "dec": 5.36902, "band": "D2",
     "size": 0.2, "role": "Nearby white dwarf, 3.0\"/yr, unsaturated: a clean motion test."},
    {"id": "wolf359", "name": "Wolf 359", "ra": 164.09237, "dec": 6.99523, "band": "D2", "size": 0.2,
     "role": "Flaring M6 dwarf, 4.7\"/yr. Independent validation target."},
    {"id": "kapteyn", "name": "Kapteyn's Star", "ra": 77.98545, "dec": -45.05966, "band": "D2", "size": 0.2,
     "role": "Halo star, 8.7\"/yr, bright (saturated). Independent validation target."},
]
