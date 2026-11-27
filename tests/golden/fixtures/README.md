# Golden-test fixtures

Five excerpts of real DASCH DR7 `lightcurve` responses, 150 rows each (about 90 KB in total). `test_lightcurve_parity.py` uses them to check that our parser agrees with daschlab 1.1.0, and `test_l1_golden.py` uses them as frozen inputs for L1 cleaning. The fifth is RY Cnc (APASS J083954.6+194919), added in Sprint 3 with `scripts/add_reference_fixture.py`.

**Source.** `POST /dasch/dr7/lightcurve` on the Starglass API, fetched 2026-11-24. The files keep the raw response format (a header line, then comma-separated rows). Each row was chosen to cover an awkward case: non-detections, blended points, missing values and points the standard rejections remove. `scripts/make_golden_fixtures.py` built the first four from the local snapshot store as it was in Sprint 1b. **Do not rerun it:** it re-selects stars by features, and the store now holds whole tiles, so it would replace them. The excerpts are enriched with awkward rows, so shares of rejected or flagged rows in them do not describe a whole lightcurve.

**Attribution.** This work has made use of data provided by Digital Access to a Sky Century @ Harvard (DASCH), which has been partially supported by NSF grants AST-0407380, AST-0909073, and AST-1313370. Work on DASCH Data Release 7 received support from the Smithsonian American Women's History Initiative Pool. See <https://dasch.cfa.harvard.edu/citing/>.

**Terms.** The Harvard terms for redistributing DASCH data have not been checked yet, and the DASCH list has been asked (`docs/outreach/dasch-email-draft.md`). If DASCH objects, delete this folder. The golden test then runs only against a local snapshot, and the unit tests keep working on synthetic rows.
