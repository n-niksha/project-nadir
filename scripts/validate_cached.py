"""Validate cached known objects without allowing archive downloads."""
import json
import logging
import sys
import time
from pathlib import Path
from unittest.mock import patch

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from nadir import config, field
from nadir.data import irsa
from nadir.jobs import Job


def main():
    logging.disable(logging.CRITICAL)
    report = []
    with patch("requests.sessions.Session.request", side_effect=AssertionError("Network disabled during cache validation")) as network:
        for preset in config.PRESETS[:3]:
            started = time.perf_counter()
            result = field.build_field(Job("field", preset["id"], field.STAGES), preset["ra"], preset["dec"], preset["size"], preset["band"])
            loaded = field.get_field(result["fid"])
            assert loaded.provenance["n_downloaded"] == 0
            field.load_catalogs(Job("catalogs", preset["id"], ["catalogs"]), loaded)
            target = min(loaded.tracks, key=lambda source: np.hypot((source.ra-preset["ra"])*np.cos(np.radians(preset["dec"])), source.dec-preset["dec"]))
            row = {"target": preset["name"], "fid": loaded.fid, "source_id": target.id,
                   "epochs": len(loaded.observations), "usable": sum(loaded.usable), "build_and_catalog_s": round(time.perf_counter()-started,3),
                   "tier": target.tier, "fit": target.fit, "catalog_comparison": (target.catalog or {}).get("pm_comparison"),
                   "catalog_errors": loaded.catalogs["errors"]}
            plan = []
            for band, records in loaded.records_all.items():
                if band == loaded.band:
                    continue
                indices = np.unique(np.round(np.linspace(0,len(records)-1, min(len(records),field.SPECTRAL_PER_BAND))).astype(int))
                plan.extend(records[index] for index in indices)
            missing = sum(not irsa.is_cached(record,loaded.ra,loaded.dec,loaded.size_deg) for record in plan)
            row["spectral_missing_cutouts"] = missing
            if target.core_masked_fraction <= 0.5:
                field.load_spectral_set(Job("spectral",preset["id"],["download","read"]),loaded,cached_only=True)
                spectrum = field.source_spectrum(loaded,target.id)
                row["spectrum"] = {"points":len(spectrum["points"]), "rejected":len(spectrum["rejected"]),
                                   "methods":sorted({point["method"] for point in spectrum["points"]}),
                                   "blackbody":{key:value for key,value in (spectrum.get("blackbody") or {}).items() if key != "curve"}}
            report.append(row)
        output = {"objects":report, "blocked_network_attempts":network.call_count}
    path = Path("artifacts/milestone3/cached-validation.json")
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(output,indent=2))
    print(json.dumps(output,indent=2))


if __name__ == "__main__":
    main()
