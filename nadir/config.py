"""Central configuration: paths, service endpoints and tunable defaults."""
from __future__ import annotations

import os
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
CACHE_DIR = Path(os.environ.get("NADIR_CACHE_DIR", REPO_ROOT / "data" / "cache"))

IRSA_BASE = "https://irsa.ipac.caltech.edu/"
IRSA_TAP = IRSA_BASE + "TAP"

# SPHEREx facts (verified against DR1/QR3 headers and the spherex.plane table).
PIXEL_SCALE_ARCSEC = 6.15
BANDS = {f"D{i}": f"SPHEREx-D{i}" for i in range(1, 7)}

# Defaults
DEFAULT_CUTOUT_SIZE_DEG = 0.2
DEFAULT_BAND = "D2"
DOWNLOAD_WORKERS = 8
HTTP_TIMEOUT_S = 120
HTTP_RETRIES = 3
TAP_CACHE_TTL_S = 7 * 24 * 3600  # archive ingests weekly
