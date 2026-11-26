"""A fake DASCH API for tests: real response format, deterministic data, injectable faults.

It implements the client's `Transport` protocol, so the real client, retry policy and
snapshot store all run unchanged against it.
"""

from __future__ import annotations

import datetime as dt
import json
import math
import threading
from collections import Counter

import numpy as np

from computers.data import dasch_parse as P
from computers.data.dasch_client import Response
from computers.data.tiles import Box, Tile, in_box


class Stop(BaseException):
    """Stands in for a Ctrl-C partway through a pull."""


def _lines(header, rows):
    return [",".join(header)] + [",".join(str(v) for v in r) for r in rows]


class FakeDasch:
    def __init__(
        self,
        tile: Tile,
        *,
        n_sources: int = 300,
        n_exposures: int = 80,
        seed: int = 7,
        max_unsplit_exposures: int | None = None,
    ) -> None:
        self.tile = tile
        rng = np.random.default_rng(seed)
        cosd = math.cos(math.radians(tile.dec0))
        self.sources = []
        for i in range(n_sources):
            ra = tile.ra0 + rng.uniform(-1.5, 1.5) / cosd
            dec = tile.dec0 + rng.uniform(-1.5, 1.5)
            mag = 99.0 if rng.random() < 0.03 else round(float(rng.uniform(8, 17)), 2)
            self.sources.append(
                {
                    "ref_text": f"APASS_FAKE{i}",
                    "ref_number": 415000000000000 + i * 7919,
                    "gsc_bin_index": 120000000 + i,
                    "ra_deg": round(float(ra % 360), 6),
                    "dec_deg": round(float(dec), 6),
                    "stdmag": mag,
                    "num_matches": int(rng.integers(20, 300)),
                }
            )
        self.exposures = []
        series = ["ai", "ac", "rh", "dnb"]
        for j in range(n_exposures):
            year = int(rng.integers(1890, 1989))
            self.exposures.append(
                {
                    "series": series[j % len(series)],
                    "platenum": 1000 + j,
                    "ra": round(float(tile.ra0 + rng.uniform(-4, 4)), 6),
                    "dec": round(float(tile.dec0 + rng.uniform(-4, 4)), 6),
                    "expdate": f"{year}-{int(rng.integers(1, 13)):02d}-15T03:30:30.2Z",
                }
            )
        self.max_unsplit_exposures = max_unsplit_exposures
        self.calls = Counter()  # by endpoint
        self.request_log: list[tuple[str, str]] = []  # (endpoint, canonical key)
        self.lightcurve_refs: list[int] = []
        self.fail_lightcurve: set[int] = set()  # these refs get a 404
        self.fail_boxes: set[int] = set()  # indexes of catalog boxes that always 504
        self.script: dict[str, list] = {}  # endpoint -> responses to give first
        self.stop_after_lightcurves: int | None = None
        self._lock = threading.Lock()

    # -- ground truth ------------------------------------------------------------

    def sources_in_tile(self):
        from computers.data.tiles import in_tile

        return [s for s in self.sources if in_tile(self.tile, s["ra_deg"], s["dec_deg"])]

    # -- Transport ---------------------------------------------------------------

    def request(self, method, url, *, json_body, headers, timeout):
        endpoint = "/" + url.split("/dasch/dr7/")[-1] if "/dasch/dr7/" in url else url
        name = endpoint.rsplit("/", 1)[-1]
        with self._lock:
            self.calls[name] += 1
            self.request_log.append((name, json.dumps(json_body, sort_keys=True)))
            queued = self.script.get(name)
            if queued:
                item = queued.pop(0)
                if item is not None:
                    return item
            stop_after = self.stop_after_lightcurves
            if name == "lightcurve" and stop_after is not None and self.calls["lightcurve"] > stop_after:
                raise Stop()
        handler = getattr(self, f"_{name}", None)
        if handler is None:
            return Response(404, b'{"error":"no such endpoint"}')
        return handler(json_body)

    def _ok(self, lines):
        return Response(200, json.dumps(lines).encode())

    def _querycat(self, body):
        box = Box(body["ra_deg"], body["dec_deg"], body["radius_arcsec"])
        for idx in self.fail_boxes:
            from computers.data.tiles import catalog_boxes

            b = catalog_boxes(self.tile)[idx]
            if abs(b.ra - box.ra) < 1e-5 and abs(b.dec - box.dec) < 1e-5:
                return Response(504, b'{"message": "Endpoint request timed out"}')
        cosd = math.cos(math.radians(box.dec))
        rows = []
        for s in self.sources:
            if not in_box(box, s["ra_deg"], s["dec_deg"]):
                continue
            dra = -((s["ra_deg"] - box.ra + 180) % 360 - 180) * cosd * 3600
            ddec = -(s["dec_deg"] - box.dec) * 3600
            rows.append(
                [s["ref_text"], s["ref_number"], s["gsc_bin_index"], s["ra_deg"], s["dec_deg"],
                 "2000.0", s["stdmag"], 0.5, 0, 0, 0, 0, 0, 1, 0, s["num_matches"], dra, ddec]
            )  # fmt: skip
        return self._ok(_lines(P.CATALOG_COLUMNS, rows))

    def _queryexps(self, body):
        ra, dec = body["ra_deg"], body["dec_deg"]
        lo = body.get("midpoint_min")
        hi = body.get("midpoint_max")
        picked = []
        for e in self.exposures:
            d = math.hypot((e["ra"] - ra) * math.cos(math.radians(dec)), e["dec"] - dec)
            if d > 4.0:
                continue
            when = e["expdate"]
            if lo and when < lo.replace("Z", ""):
                continue
            if hi and when > hi.replace("Z", ""):
                continue
            picked.append(e)
        limit = self.max_unsplit_exposures
        if limit is not None and lo is None and hi is None and len(picked) > limit:
            return Response(502, b'{"message": "Internal server error"}')
        rows = []
        for e in picked:
            # These three depend on the queried position, as in the real API.
            dist = round(math.hypot((e["ra"] - ra) * math.cos(math.radians(dec)), e["dec"] - dec), 3)
            rows.append(
                [e["series"], e["platenum"], 0, 0, 0, 0, "", e["ra"], e["dec"], "60.00",
                 e["expdate"], 2000, "imwcs", "2018-01-01T00:00:00", "2020-01-01T00:00:00",
                 dist, round(10 - dist, 3), round(12.5 - dist / 10, 3), "", "0.1", "", 1, 1, 1, 1, "", ""]
            )  # fmt: skip
        header = [
            "series", "platenum", "scannum", "mosnum", "expnum", "solnum", "class", "ra", "dec",
            "exptime", "expdate", "epoch", "wcssource", "scandate", "mosdate", "centerdist",
            "edgedist", "limMagApass", "limMagAtlas", "medianColortermApass",
            "medianColortermAtlas", "nMagdepApass", "nMagdepAtlas", "nSolutionsApass",
            "nSolutionsAtlas", "resultIdApass", "resultIdAtlas",
        ]  # fmt: skip
        return self._ok(_lines(header, rows))

    def _lightcurve(self, body):
        ref = body["ref_number"]
        with self._lock:
            self.lightcurve_refs.append(ref)
        if ref in self.fail_lightcurve:
            return Response(404, b'{"error":"unknown source"}')
        rng = np.random.default_rng(ref % (2**32))
        n = 30
        rows = []
        for k in range(n):
            e = self.exposures[int(rng.integers(0, len(self.exposures)))]
            nondet = k % 7 == 3
            flags = 0 if rng.random() > 0.3 else int(1 << int(rng.choice([6, 11, 12, 13, 15, 25])))
            row = {
                "date_jd": 2415000.0 + k * 150.5 + float(rng.random()),
                "series": e["series"],
                "plate_number": e["platenum"],
                "mosaic_number": 0,
                "solution_number": 0,
                "exposure_number": "" if nondet else 0,
                "limiting_mag_local": round(float(rng.uniform(11, 14)), 4),
                "ref_number": "" if nondet else ref,
                "gsc_bin_index": "" if nondet else body["gsc_bin_index"],
                "sextractor_number": "" if nondet else int(rng.integers(1, 9999)),
            }
            for col in ("magcal_magdep", "magcal_local_rms", "fwhm_pix", "ellipticity",
                        "plate_center_dist_deg", "ra_deg", "dec_deg", "x_image", "y_image",
                        "time_accuracy_days", "extinction"):  # fmt: skip
                row[col] = "" if nondet else round(float(rng.uniform(5, 15)), 4)
            row["blended_mag"] = ""
            row["drad_rms2"] = "" if nondet else round(float(rng.uniform(0.1, 3)), 3)
            for col in ("aflags", "a2flags", "bflags", "b2flags", "reject_flag"):
                row[col] = "" if nondet else (flags if col == "aflags" else 0)
            rows.append([row[c] for c in P.LIGHTCURVE_COLUMNS])
        return self._ok(_lines(P.LIGHTCURVE_COLUMNS, rows))


def iso(year: int) -> str:
    return dt.datetime(year, 1, 1, tzinfo=dt.UTC).isoformat()
