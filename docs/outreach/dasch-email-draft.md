# Draft email to the DASCH Astrophysics list

**Status: draft for you to edit and send. Nothing here has been sent.**

## Before you send

1. **Join the list first.** DASCH's own page says to subscribe to the DASCH Astrophysics email list for discussion, and that bulk-data requests go through it. Join at <https://gaggle.email/join/dasch@gaggle.email>, then post to `dasch@gaggle.email`.
2. **Fill in the three bracketed items** below: your name, the repo link, and the pilot numbers (after you run the pilot).
3. **Read it as the DASCH team would.** It is long on purpose, because every question comes from something measured. Cut the questions you care about least. The first three matter most.
4. This draft contains no API key and no private data. Keep it that way.

---

**Subject:** DR7 API question: rate limits, bulk export for three 2-degree tiles, and a few behaviours I measured

Hello,

I am [your name]. I'm building an open-source (AGPL-3.0) triage tool that patrols three 2 by 2 degree DASCH tiles (M44, the field of KIC 8462852, and the field of T CrB) for stars that brightened or faded, and measures how often it is right by injecting known signals into real lightcurves. Repository: [link]. I use the DR7 web APIs directly through a small client with retries, and I store every response so nothing is requested twice. DASCH and daschlab are acknowledged in the README as your citation page asks.

Thank you for DR7, and for the clear documentation. A great deal of this worked the first time.

## 1. Request volume and rates (the main question)

For the three tiles, once, I plan about:

- 12 `querycat` calls (four per tile, radius 1,850 arcsec each) and 15 `queryexps` calls;
- 1,688 `lightcurve` calls in total, for stars with a catalog B magnitude of 13.5 or brighter (444 in the M44 tile, 186 in the T CrB tile, 1,058 in the Tabby's Star tile).

The client uses at most 3 requests in flight, at least 0.2 s between request starts, exponential backoff on 429 and 5xx responses, and it never repeats a request it has already stored. Measured on the full API (with a Starglass key): a `lightcurve` call takes a median of 0.54 s, `querycat` at 1,850 arcsec about 17 s, and `queryexps` 16 to 20 s (95th percentile 23 s). At 3 requests in flight I reached 4.5 requests per second with no 429 responses. On the public API I reached only about 1.2 requests per second and received eight 429 responses, so I dropped to 1 request in flight there.

- Are these rates acceptable? Is there a request rate you would like me to stay under for the full API?
- Would a **bulk export** of the lightcurves for three 2-degree tiles (a few thousand stars each) be easier for you than many individual calls? I am happy to take whatever format is simplest for you.

## 2. Behaviours I measured that differ from the documentation

I list them in case they help, and to check I'm not misreading something.

- **`querycat` radius.** The Web API page gives a maximum of 3,600 arcsec. The API answers `HTTP 400 illegal radius_arcsec parameter` for 3,600 and 3,601, and `HTTP 504` (timeout after about 30 s) for 3,000 and 3,599. 2,400 worked but took 30.5 s. Query time grows with box area, not with the number of stars (1,800 arcsec took 17 s for 1,473 sources and 19 s for 11,594 sources). I use four boxes of 1,850 arcsec per tile.
- **Column naming.** The page says raw responses use camelCase names. In my tests `querycat` and `lightcurve` use snake_case (`date_jd`, `plate_number`, `magcal_magdep`, ...) and only `queryexps` uses camelCase (`limMagApass`, `resultIdApass`, ...).
- **Row order.** Two requests for the same star return the same rows, but rows with identical timestamps come back in a different order each time (67 of 4,740 positions differed between two fetches). Is there a canonical order I should sort by?
- **Response size.** daschlab's client says responses are limited to 6 MB. `queryexps` for one position returned 2.9 MB for 16,326 exposures. Is a denser field likely to exceed the limit, and what does the API return when it does?
- **Reduction guide.** It imports `AFlags` from `daschlab.lightcurves`, but in daschlab 1.1.0 the class is in `daschlab.photometry`.

## 3. Data questions

- **Plate class in `queryexps`.** The `class` column is empty for every exposure I checked, about 49,000 across the three fields. `GET /plates?ids=...` does return a class for about 93% of the plates I sampled, though some values look like free text (`Plate 5`, `w`, `PAN`). Is the empty column in `queryexps` expected? I'll use the colourterm for emulsion, as your Colorterms page recommends. For my T CrB star, 95.4% of detections are on plates with a blue colourterm, and the red and yellow ones are from `dnr` and `dny`, as I'd hope.
- **Re-scanned plates.** For about 19% of the detections of T CrB, the lightcurve row names a mosaic number (for example `m0`) that `queryexps` does not list for that plate (it lists `m1`). Joining on the plate and exposure number works for all of them. Which mosaic does the photometry come from, and is it still valid?
- **`UNCERTAIN_CATALOG_MAG`.** I find the per-point AFLAGS bit matches the catalog `v_flag` for all four stars I checked, and that `v_flag` is 1 for 97% of the sources within 14 arcmin of T CrB. Is the bit simply a copy of the catalog flag?
- **Standard rejections and bright outbursts.** `apply_standard_rejections()` removed 25 of the 32 T CrB measurements from 1946 that lie at least 2 mag above its baseline, mostly through `LARGE_ISO_RMS`, `LARGE_LOCAL_SMOOTH_RMS` and `BIN_DRAD_UNKNOWN`. I assume that is expected for saturated images. Is there a recommended way to keep real outburst points while rejecting defects?
- **Undocumented columns.** What are `reject_flag` (I read it as `local_bin_reject_flag`), `pass_bits`, `version_id`, `catalog_number` and `mask_index` in the `lightcurve` response?
- **Timestamps.** `expdate` in `queryexps` appears in three forms (with and without a trailing `Z`, and minute-only), and one value has 60 seconds (`1970-06-03T05:07:60.0Z`). I read that as 05:08:00. Is that right?

## 4. Publication

- May I publish **small excerpts** of lightcurve responses as test fixtures in the public repo? I have included four 150-row excerpts, about 75 KB in total, with attribution, and will remove them if you prefer.
- Later I would like to publish a **labelled set of cutout crops** (real stars versus plate defects, with defect types) and the injection benchmark. What are the terms for publishing derived products of this kind? I'd be glad to share the labelled set back with DASCH.
- Could I request the **logbook pages** for a handful of specific plates (the ones around the 1946 T CrB eruption)? I'd list the plate IDs.

## What I'd offer back

The defect-labelled cutout set, the injection-recovery benchmark and its completeness curves, and the full list of the behaviours above with the commands that reproduce them, if any of it is useful to you.

Thank you,
[your name]
[affiliation, if any]

---

## Where each number comes from (not part of the email)

| Claim | Source in this repo |
|---|---|
| `querycat` radius limits and timings | `docs/decisions/0001-sprint0-findings.md`, W1 |
| camelCase versus snake_case | same, W2 |
| Row order differs between calls | `.kiro/specs/s1b-thin-client/` and `tests/golden/test_live_parity.py` |
| 19% of T CrB detections reference a mosaic not in `queryexps` | `docs/decisions/0003-tile-sizes-and-access.md` |
| Plate class empty in `queryexps`; 93% present via `/plates` | `data/spike/access-checks.json` (`dasch_plate_class`) |
| 95.4% blue by colourterm | `docs/decisions/0003-tile-sizes-and-access.md` |
| 25 of 32 eruption points removed | `docs/decisions/0001-sprint0-findings.md`, W4 |
| `v_flag` equals `UNCERTAIN_CATALOG_MAG` | same, W6 as corrected in `0003` |
