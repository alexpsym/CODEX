# Phase 7B1 COMPLETE - five-document recovery pilot, 20 October 2026

This current status supersedes the historical pause checkpoint below. Exactly five manifest-selected documents were processed; the other 97 documents remain future work. All five Word originals, shortcuts, videos and journal cells remain unchanged. No original is deletion-eligible.

Published beside the five selected originals under C:/Users/User/Dropbox/FOREX:
- 18 complete logical table PNGs, with measured cells, readable 18px Arial text, merged-cell coordinates, original labels/values/precision and supported emphasis/borders.
- Five embedded PNG pictures preserved byte-for-byte at original resolution.
- Four UTF-8 text companions preserving meaningful body/header/text-box prose and supported bullet labels in source context.

Private publication evidence and exact output list:
C:/Users/User/AppData/Local/Temp/forex_word_7b1_resume_4eb128350c0442ae8ba8e305c478497e

Use published_outputs.txt for exact source/output locations; publication.json for per-file hashes/completion and five original-hash/before-after checks; published_verification.json for independent final reconciliation. selection.validated.json, preparation.json, document_01 through document_05/provenance.json and captured.docx retain source identities/hashes, exact structured tables, merge coordinates, prose, image relationships/occurrences and metadata bytes. private_checks.json, independent_validation.json and visual_review.json retain preparation and visual-review proofs. All real source filenames/content/manifests remain outside Git.

The original exact selection remains:
C:/Users/User/AppData/Local/Temp/forex_word_7b1_40543b9208e94479b074d3c9c124c9d8/selection.json

The old evidence/captures/outputs and failed-attempt records are preserved unchanged. Preparation resumed from those five verified captured copies (815,270 bytes), with ZERO original recaptures or archive rescans. All 27 prepared user-facing output hashes matched the prior outputs exactly. The same four previously viewed images were proven byte-identical, retaining the prior three-table/one-picture visual checks without any new image views. Table bounds, embedded bytes, output decoding, exact text/coverage and known sample counts reconcile. There are 26 raw XML table occurrences mapped to 18 logical table images, including eight explicitly matched Choice/Fallback aliases.

The only blocker was narrowed safely: the canonical tool now recognizes an empty bibliography Sources element paired through the customXmlProps relationship with matching bibliography schema properties. It preserves all 12 encountered Custom XML/store-property/relationship parts byte-for-byte as base64 plus SHA256 in private provenance. Nonempty stores, unknown schemas/attributes, data bindings and other unsupported Custom XML remain blocking. Nothing fetches the bibliography style path or external relationships.

Canonical implementation: tools/recover_forex_word_documents.py, with manifest-driven default private preparation, optional --saved-captures for exact-selection source-free re-preparation, private verification and explicit publication. Exactly five distinct manifest-listed DOCX sources are required. Publication validates the selection/tool/preparation hashes, review record, every original hash and every output destination before staging; source read-share-only handles stay open during Windows publication, existing files are not overwritten, and successful final files are atomically exposed only after complete private bytes are staged. No deletion/journal-update options exist.

Resumed targeted verification:
- Exactly the same two affected nodes in tests/test_recover_forex_word_documents.py; ONE resumed host pytest process, BOTH PASS (16.27 pytest / 18.703218 outer seconds), fresh explicit host GUID parent/basetemp and 180-second hard bound. This follows the two historical pre-pause processes; no unchanged passing reruns or extra nodes.
- Tests exercise parser/render/publication end-to-end on synthetic FOREX fixtures, exact embedded bytes, header images/prose, multiline/Unicode/tiny-price/zero/No values, horizontal/vertical merges, table bounds, duplicate branches, metadata-byte provenance, source-free saved-copy preparation, changed/missing sources, collisions, unsafe/non-FOREX/oversized selections and linked/unsupported/nonempty/unknown/bound metadata rejection.
- One renewed no-source canonical CLI preflight PASS 0.533808 seconds, 30-second bound, independently reopened in a later host command.
- One saved-copy preparation PASS 4.717629 seconds, 180-second bound; no source reads/captures in that stage.
- One actual publication PASS 1.651222 seconds, 60-second bound; five originals each hash-checked once at publication and unchanged size/mtime afterwards. Combined resumed preparation/publication 6.368851 seconds.
- All 27 published files independently rehashed against the private manifest; zero task staging files remain. Four historical image views total, zero new views. At publication verification, resumed global elapsed was 129.653200 seconds of the 720-second ceiling; subsequent scope/delivery checks remain bounded.

Resumed local/remote baseline 983e47287bd3538674622b47e345d6c1d169a40b includes an unrelated FT6 preset fix since the pause; its committed summary and all unrelated working-tree changes were preserved. Only the canonical tool, its two-node test file, this plan and the appended summary are task-owned for delivery. The pause notice/history below is preserved. All printing work remains cancelled; accepted Bybit/phases 1-5 and existing feeds stay unchanged, OANDA 2B cancelled; Instrument Lookup image/precision work stays queued.

Next bounded deliverable: a separately authorized remaining-document extraction batch from the retained manifest, initially at most 20 of the remaining 97, with per-document content checks and collision/source-change protection. The current CLI deliberately retains the exact-five pilot restriction; widen it only in a separately reviewed batch change. No further document processing, journal backfill or shortcut/original deletion was performed or authorized here. Future 7C still requires proven trade identities and true blanks only; future 7D requires verified complete recovery and proven actual-video shortcut targets, preserving real videos and unresolved/non-video links.

---

# Phase 7A Forex Word archive inventory and handoff

19 October 2026. **Phase 7A complete for the bounded local-tree inventory and three captured samples.** This does not establish cloud completeness or the contents of every document. Only inventory and planning were authorized; 7B/7C/7D remain future work.

All printable-journal Phase 6A/6B work remains cancelled, including layout, full PDF export and Journal/LTT print/PDF menu work. Prior code/artifacts remain. Phases 1-5 and urgent Bybit fixes remain accepted; OANDA 2B remains cancelled and existing feeds remain unchanged. Instrument Lookup PNG/JPG download remains queued; assess remaining symbol precision against existing delivery before proposing changes.

## Retained private evidence

Confirmed root: C:/Users/User/Dropbox/FOREX, using the previously confirmed Dropbox personal base. No root rediscovery, alternate archive scan or cloud traversal occurred during recovery.

Host evidence directory: C:/Users/User/AppData/Local/Temp/forex_word_7a_host_46efeff728734c77be80a4f196803cff

- manifest.jsonl: incremental per-entry paths, parent trade folders, F-number/symbol evidence, extension, size, mtime, attributes, availability and skipped-link status.
- start.json/root.json: root identity, explicit output, executable, worker hash and limits.
- enumeration.json/final.json/errors.jsonl: reconciled counts, completion and stopping point; no remaining directories or enumeration errors.
- samples.json/sample_01.docx through sample_03.docx: source provenance, before/after size/mtime, captured bytes and SHA256.
- inspection.json: exact private table text/rows, prose, header/footer content, media entries, relationships and embedded-object findings.
- preflight.json/preflight_outer.json/preflight_verified.json, inventory_outer.json, verification.json and aggregate_handoff.json: durable storage proof, timing and independent result checks.
- inspect.py/capabilities.json: private worker and observed inert inspection capabilities.

One no-source host preflight used the exact executable/launch boundary/worker and absolute output argument used by the inventory: Python C:/Users/User/miniconda3/python.exe. It passed in 0.519943 seconds with a 30-second hard child timeout. A subsequent independent host command reopened the exact dummy record and verified output, executable, worker availability and SHA256 before source access.

The first attempt remains consumed and its original sandbox evidence remains unavailable. Its separate aggregate failure checkpoint C:/Users/User/AppData/Local/Temp/forex_word_7a_checkpoint_0636673facc546619bad4cd72f090aca/checkpoint.json was preserved, without searching or repeating recovery checks. Current findings come from the newly authorized host attempt.

## Coverage and scale

One renewed source worker/process, hard 300-second timeout and 50,000-entry ceiling, finished in 2.418901 seconds; enumeration took 1.626611 seconds. All years/nested folders were eligible. Counts reconcile with saved records:

| Path year | Entries | Folders | Documents | Shortcut candidates |
| --- | ---: | ---: | ---: | ---: |
| 2018 | 1,246 | 382 | 102 | 78 |
| 2019 | 1,423 | 252 | 0 | 0 |
| 2020 | 614 | 104 | 0 | 0 |
| 2021 | 469 | 170 | 0 | 0 |
| 2022 | 293 | 57 | 0 | 0 |
| 2023 | 192 | 44 | 0 | 0 |
| 2024 | 125 | 20 | 0 | 0 |
| 2025 | 83 | 13 | 0 | 0 |
| 2026 | 22 | 9 | 0 | 0 |
| Total | 4,467 | 1,051 | 102 | 78 |

The remaining 3,236 entries are other files, listed by metadata without reading pictures/videos. All 102 document extensions are .docx; all 78 shortcut extensions are .lnk. No .doc/.docm/.dot/.dotx/.dotm/.rtf/.odt, lock documents, .url or .website candidates were found. All documents have a unique path F-number as inventory evidence; no journal identity matching has been performed.

All 102 documents are metadata-local candidates. No cloud-placeholder tags, recall/unavailable flags, permission errors, enumeration errors or skipped links were observed. Only the three captured documents have verified readable/stable bytes; these flags do not prove all other files are downloaded or that the local tree is cloud-complete. Shortcut targets remain unknown and were not resolved.

## Bounded sample findings

Selection used size extremes then median from the same traversal's metadata, preferring three distinct trade folders. Each was captured once with read-only/no-recall Windows open flags and write sharing denied; before/after size/mtime match. Independent host reads verified all captured hashes, sizes, source-record membership and inspection linkage. Total captured bytes: 395,020, within three samples/20 MiB each/50 MiB combined.

| Private sample | Bytes | Embedded media | Tables including nested | Nested tables | Header parts | Nonempty prose paragraphs |
| --- | ---: | --- | ---: | ---: | ---: | ---: |
| 1 | 12,583 | 0 | 2 | 0 | 0 | 0 |
| 2 | 359,136 | 2 PNG | 6 | 0 | 1 | 21 |
| 3 | 23,301 | 0 | 6 | 0 | 1 | 7 |

All three are valid Word OOXML packages. Across these samples: 14 tables, two embedded PNG entries and two image relationships. No external relationships, linked images, embedded packages/objects, field instructions or alternate-content chunks were observed. Header prose is included in the prose counts; no footer parts were present. Samples 2 and 3 include footnote/endnote parts with no nonempty prose or tables. Exact table labels/text and prose remain private.

Observed host Python 3.13.2 on Windows 11; inspection used only standard-library zipfile, ElementTree and hashlib. No native Office/parser/renderer capability is claimed. No image/table extraction, rasterization, PDF, external fetch, dependency installation or tests occurred. Structural inspection does not validate visual rendering, image decoding or complete preservation fidelity. Findings apply only to these samples, not all 102 documents; absence of unsupported formats here does not justify treating them as empty if encountered later.

## Smallest next deliverable: proposed Phase 7B pilot

Authorize a five-document pilot selected from the retained manifest, including the three sampled structures and two additional sources. Avoid another whole-tree scan. Validate every picture and table, structured values and non-table content for this pilot before scaling. With 102 known documents, later batches of at most 20 leave 97 documents after the pilot (four batches of 20 and one of 17); reduce batch size if complexity requires it. No extraction implementation is included in 7A.

7B must extract embedded pictures and render every trade-data table to image files beside its source, preserving structured table labels/values and source-to-output provenance. Use deterministic source-specific output names and reject existing-name collisions or source changes rather than overwriting. Record source path/size/mtime/hash, package-part or table identity, output hash and per-item verification. Inventory each batch's actual content, including nested/header/footer tables, linked media, embeddings and relevant prose; reconcile all required outputs and verify readable, unclipped table rendering. The observed prose/header content means pictures and table images alone cannot establish deletion readiness. Unresolved/unsupported content or incomplete recovery blocks original deletion eligibility.

7C must prove trade identity from unambiguous F-number/path plus corroborating evidence, rejecting missing/conflicting identities and fuzzy symbol-only matches. Fill only genuinely blank journal cells; zero, False, formulas and existing text are preserved, together with every existing value and workbook style/layout/formula. No workbook matching or access occurred here.

7D requires verified complete recovery before a Word original becomes deletion-eligible. Inspect shortcut targets inertly only in a later authorized batch and confirm actual video targets. Preserve unresolved/non-video links, real video files and unrelated content. The 78 .lnk candidates are not 78 proven video shortcuts. No deletion is authorized.


## PAUSED by user - Phase 7B1 checkpoint, 19 October 2026

This checkpoint supersedes the earlier proposed-pilot status above. The user requested a pause at a safe boundary: no operation was running. No further recovery, tests, commits or pushes were started after that request. All changes remain uncommitted; no pilot output was published into FOREX.

Current stage: canonical implementation and focused tests written; final tests and host preflight passed; the single actual five-document preparation finished with INCOMPLETE status. Document 1 is privately prepared and structurally complete. Documents 2-5 have private pictures/tables/text prepared but are blocked by the conservative Custom XML guard. The retained copies show an empty bibliography Sources element with settings attributes and associated datastore properties. This is a metadata-classification gap, not a proven loss of trade text. No guard was bypassed and no code/test changes followed the final passing test run.

Intentionally changed task files (all uncommitted):
- tools/recover_forex_word_documents.py - new canonical selection-driven preparation/publication CLI.
- tests/test_recover_forex_word_documents.py - two new synthetic-only regression nodes.
- docs/forex_word_archive_plan.md - this appended pause checkpoint; earlier content retained.
- summary/CODEX_SUMMARY_latest.txt - short appended pause note; earlier bytes retained.

Preserve all pre-existing unrelated modified/deleted/untracked work and the empty initial index. Baseline remains bb26fbc5b143dfa5b5a6db549cebf04e1a713d22; fresh remote matched before implementation. No task commit/push has occurred. Do not run a blanket staging/delivery script.

Private host evidence/output directory:
C:/Users/User/AppData/Local/Temp/forex_word_7b1_40543b9208e94479b074d3c9c124c9d8

selection.json persisted the exact five manifest-listed sources before source access: the three Phase 7A samples plus two additional sources from distinct trade folders selected by metadata size. Exact source paths remain private. document_01 through document_05 each contain captured.docx, provenance.json and prepared outputs. selection.validated.json/preparation.json contain source/output mapping and hashes; prepare_outer.json/prepare_failure.json record the incomplete attempt. saved_evidence_checks.json, text_reconciliation.json and visual_review_checkpoint.json record saved-only checks and the blocker. preflight.json/preflight_outer.json/preflight_verified.json prove host storage, executable, Pillow 11.2.1 and four installed Arial variants at 18px.

All five captures were stable (815,270 bytes total), using read-only/no-recall access with before/after metadata and SHA256; the three known sample hashes matched. Original documents, shortcuts, videos and journal cells were unchanged. Prepared privately: 18 logical table PNGs, five original embedded PNG pictures and four UTF-8 text companions. There are 26 raw XML table occurrences, including eight matched modern/fallback aliases mapped to logical table outputs. Source/output hashes, exact text, structured cell/merge provenance and measured bounds are retained privately. Source text reconciliation and embedded-byte/output decoding checks passed. Four distinct images were viewed (three tables and one picture), with readable tables and no observed clipping. These diagnostic checks do not override the failed preparation status. Published documents/files: ZERO; publication process/approval was never started.

Budgets used and remaining:
- Two unique planned pytest nodes, two host pytest processes used; zero test-process allowance remains. First run failed in fixture argument naming before recovery execution (1.01 pytest / 2.603122 outer seconds). Fixture corrected; final run both PASSED (7.70 pytest / 8.795131 outer seconds), each within 180-second hard bound and fresh host GUID basetemp. No later source/test edits.
- Failed-run evidence: C:/Users/User/AppData/Local/Temp/forex_word_7b1_tests_657a10d62c7a4f1bab2570ce99ac7021/result.json.
- Passing-run evidence: C:/Users/User/AppData/Local/Temp/forex_word_7b1_corrected_tests_7d4dd1bbb90041129e1501d2ed4229c3/result.json.
- One no-source canonical preflight used, PASS 0.391354 seconds / 30-second hard bound; independently reopened in a subsequent host command. Zero preflight retry allowance.
- One real preparation process used, 3.062748 seconds / 180-second hard bound; INCOMPLETE, five sources captured once each, no recapture/rescan allowance remains.
- Publication used zero processes / zero seconds; nominal 60-second stage allowance was not exercised, but publication is NOT authorized after this failed preparation. Combined actual-stage time used 3.062748 seconds against the 240-second cap; unused seconds do not authorize a retry.
- Four distinct image views used; zero image-view slots remain under this brief.
- Timed test/preflight/preparation outer components total 14.852355 seconds. Overall verification cap is 720 seconds; an exact cumulative counter including Git/read/diagnostic/view overhead was not retained, so no precise remaining global-time figure is asserted. Preserve these component timings and establish explicit renewed allowances before continuation that needs them.
- Archive rescans, deletion, journal operations, Office/printer/broker/trading actions, dependency/system/config changes: zero.

Exact next safe action when resumed: read this checkpoint and retained private failure/metadata evidence, then establish renewed authorization/budgets for a narrow canonical metadata-classification/preservation correction and targeted regression coverage. Preserve the guard for unknown/nonempty Custom XML; do not apply a blanket bypass. Use the already hash-verified captured copies for any newly authorized re-preparation, without recapturing originals or scanning the archive. Publication requires a newly authorized successful preparation/verification lifecycle, exact selection validation and bounded original-hash/collision revalidation. Do not publish under the failed attempt or reuse the exhausted test/image allowances. No remaining 97-document batch, journal backfill or deletion is authorized.

On resume, review any concurrent repository/remote changes and all current uncommitted hunks before implementation or delivery. Commit/push only after the user resumes and authorizes that work; this pause explicitly prohibits them. All printable-journal work remains cancelled; other accepted phases/feeds remain unchanged.


## Delivery checkpoint - concurrent master advance, 20 October 2026

The five-document pilot publication is complete and independently verified as recorded above. Repository delivery remains INCOMPLETE. Before any staging/commit, the fresh-remote guard detected master advancing from resumed baseline 983e47287bd3538674622b47e345d6c1d169a40b to f5d89e63d0de3ca45b6b02b3aca5d74e37e3874e. The shared local HEAD and origin/master also advanced to that same new SHA. The Phase 7B1 brief explicitly requires stopping on remote advance; no pull/rebase/reset, staging, task commit or push was performed.

The four task files remain uncommitted, along with all pre-existing unrelated work. The new concurrent summary content is preserved. All published outputs/private evidence and original source documents remain intact. No publication retry or additional recovery work is required.

Exact next safe action: authorize/reconcile delivery against current shared master f5d89e63d0de3ca45b6b02b3aca5d74e37e3874e, review any intervening commit and the four task-owned hunks while preserving concurrent summary changes, then perform scoped whitespace/staged/actual commit-scope checks and one exact-path --only commit/protected-helper push. Recheck fresh remote; stop again if it advances. Reuse the already passing tests and verified publication; do not recapture/rerender/republish or repeat unchanged passing tests. No delivery SHA exists for this pilot yet.


## Scoped delivery reconciliation - 20 October 2026

The user's Phase7B1 Scoped Delivery brief explicitly supersedes the previous stop for six known commits. Verified their linear ancestry from bb26fbc: 983e472 (FT6 strategy, focused C++ test, FT6 guide and separate summary block), f5d89e6 (FT6 deployment summary only), then 53c8366/a964ca1/961b60e/1bc15ad (each only journal/Trading Journal.xlsx). Adopted common master/local/origin/fresh-remote baseline 1bc15ad5d1f2fa372e32179d6a3f0c89d601f2e7. No workbook was opened/read as application data or modified. FT6 committed summary hunks and all unrelated user/runtime changes remain intact; task-only uncommitted summary hunks were reviewed separately.

Delivery reuses the saved passing two-node record and completed five-document publication ledger. The canonical tool SHA256 matches retained passing preflight/preparation evidence; full current test source matches the retained authored patches/checkpoint, with its modification predating the passing invocation. Source and test hashes are retained in scoped_delivery_review.json. No code/test edits, tests/builds/smoke checks, archive/source/output reads, image views, recovery or publication occurred in this delivery-only continuation.

Saved publication records still report five originals preserved and 27 published files (18 table PNGs, five original pictures, four text companions); no additional data revalidation was performed. Exact outputs remain listed privately in published_outputs.txt in the retained resume evidence directory above. Pilot scope remains five of 102; all remaining recovery/backfill/cleanup work is future authorization.

This reconciliation and appended summary precede ONE exact-path stage/--only commit for the existing canonical tool, focused test file, this plan and the shared summary's task additions. Actual staged/commit file lists and whitespace are checked; normal protected-helper push only. Actual final SHA and verified final tip agreement are reported separately after delivery; no second summary-only commit.
