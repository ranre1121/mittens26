# HANDOFF — finish and ship the ForteBank cashier-video service

Written 2026-10-18 by the previous agent session for the next AI agent.
Goal: **a deployed service that returns real `violation` / `no_violation` verdicts
with timecode, evidence frames and a legal reference**, through the jury API.
Ship fast; correctness of the jury JSON matters more than elegance.

Read these first:
- Case spec (Russian): `~/Downloads/Кейс_ForteBank.docx` (PDF copy:
  `~/Downloads/Telegram Desktop/Кейс_ForteBank.docx.pdf`). Key points are summarised below.
- Legal research: `docs/regulatory-research.md` (NBRK Resolution No. 231, No. 14, etc.,
  with paragraph-level rule → observation table).
- Violation vocabulary: `rules/vocabulary.json` (15 types, Russian labels, rule ids,
  training clips). Rule registry: `rules/registry.json`.

---

## 1. What the jury requires (from the case spec)

- `POST /api/v1/analyze` (multipart field `file`, `.mov`, ~29 MB, header `X-API-Key`) → `{"job_id": ...}`.
- `GET /api/v1/jobs/{job_id}` → `processing` | `failed` | `done`. When done:
  ```json
  {"job_id": "...", "status": "done", "verdict": "violation",
   "violations": [{"type": "Название типа", "timecode_sec": 44.2,
     "description": "Что видно и почему это может быть нарушением",
     "evidence_frames": [{"timestamp_sec": 44.2, "image_base64": "/9j/..."}]}]}
  ```
- **`verdict` must be exactly `violation` or `no_violation`.** Today we return
  `insufficient_evidence` → wrong. "Not enough data" abuse is penalised.
- 1–3 JPEG evidence frames per violation. Timecode must point at the right frame.
- **5-minute limit per video** (else counted unprocessed). Videos 10–145 s, ≥29 fps, top-down, one camera, one till; final test video filmed **from a different angle**.
- **A missed violation is unacceptable; false positives should be minimal.** Scoring is automatic, per-video verdict.
- Test may contain violation types not in training data.
- Must run locally: no cloud AI APIs (Qwen on our own GPU is fine). No face recognition. No audio.
- Rule references: only Kazakhstan acts (NBRK, adilet.zan.kz); no invented references.
- Scoring (100): detection 25, localisation (timecode + frame) 15, type classification 15,
  rule justification 15, robustness (unseen angle) 10, product readiness 10, architecture 10.
- Deliverables besides the service: public URL + API key for jury, run/usage instructions,
  architecture description, list of detected violation types, legal sources + how rules map
  to video, known limitations. **Deadline 12:00** (check which day with the user).

## 2. What is live right now

```
Browser / jury ──HTTPS──> Cloudflare Worker (FastAPI on Pyodide) ── D1 (jobs) + R2 (videos, results, evidence)
                                   ^
                                   | HTTPS pull (outbound only; venue network blocks inbound/tunnels)
                                   |
             Brev "analyzing-machine" (L40S 46 GB, id 62x7fnhd6, ~$1.06/h)
               app.remote  → local backend pipeline → QWEN_PROVIDER=command
                           → qwen_runner.client → Unix socket → qwen_runner.daemon (Qwen resident on GPU)
```

- Site + API: **https://datathon26.pognar1121.workers.dev** (no login on the site, by decision).
  `GET /api/health` shows `"analysis": "connected"` when the GPU worker polls.
- Jury key: `API_KEYS` in `.secrets.prod.local` (gitignored). Analyzer token: `ANALYZER_TOKEN` in the same file.
  **Never print or commit them.**
- E2E verified: 30 s clip → done in 16 s via the jury API, real Qwen (`is_mock: false`).
- **Problem:** every job returns `verdict: insufficient_evidence`, `violations: []` (see §4).

### Code (all UNCOMMITTED in this working tree — commit before anything else)
- `backend/qwen_runner/` — NEW. Port of the team's qwen-pilot:
  `windows.py` (windows from sampled frames, prompt messages, strict per-event validation,
  mapping to backend events), `model.py` (Qwen3-VL-8B BF16 on CUDA, batched generate, optional
  LoRA via `QWEN_LORA_DIR`), `daemon.py` (resident model on a Unix socket), `client.py`
  (the executable `QWEN_COMMAND_JSON` points to), `prompt-v2.txt` (pilot's revised prompt).
- `backend/app/remote.py` — NEW. Pull worker: claims jobs from the Worker, downloads video,
  runs the normal local `Worker.run_once()`, uploads cited evidence frames + result.
- `worker/src/main.py` — `/api/analyzer/*` routes (claim, video download, progress heartbeat,
  evidence upload, complete, fail), stale-job requeue after 120 s, `analysis` in UI view,
  fixed R2 `memoryview` bug (evidence images were 500) and D1 batch bug.
- `worker/migrations/0002_analyzer.sql` — `jobs.heartbeat_at`, `analyzer_status` table.
  **Already applied to production D1.**
- `scripts/brev-analyzer.sh` — setup/start/stop for the GPU box.
- `backend/tests/test_qwen_runner.py` — 6 tests. Full suite: **140 passed** (`cd backend && .venv/bin/python -m pytest -q`).
- Also uncommitted from earlier: UI key-login session (`backend/app/session.py`,
  `frontend/src/components/AuthGate.tsx`, `frontend/src/lib/session.ts`, …). The Worker answers
  `/api/session` with 404, so the live site stays open; harmless.

### GPU box layout (`analyzing-machine`)
- SSH: `ssh -i ~/.brev/brev.pem shadeform@216.81.200.24` (port 22 works; Brev relay ports are blocked on the venue network).
- `/ephemeral/kc/venv` (py3.12, torch 2.10 cu126, transformers 4.57.6, peft, bitsandbytes),
  `/ephemeral/kc/hf/...snapshots/0c351dd01ed87e9c1b53cbc748cba10e6187ff3b` (Qwen weights),
  `/ephemeral/kc/repo/{backend,rules}` (rsync of this repo — dir must NOT be named `app`),
  `/ephemeral/kc/lora/` (experimental pilot adapter), `daemon.env`, `analyzer.env` (has the token),
  `start-daemon.sh`, `start-analyzer.sh`, logs `daemon.log`, `analyzer.log`, raw model output `qwen-log/`.
- Deploy code change: `rsync -az --exclude .venv --exclude '*.egg-info' --exclude __pycache__ backend rules shadeform@216.81.200.24:/ephemeral/kc/repo/`
  then `ssh … /ephemeral/kc/start-daemon.sh` (if qwen_runner changed) and `/ephemeral/kc/start-analyzer.sh`.
  Don't `pkill -f qwen_runner` over ssh — it kills your own ssh shell; use the pid files.
- Model version string the backend checks: `QWEN_MODEL_VERSION=0c351dd01ed8+prompt-v2` (daemon logs it at startup).

### Second Brev VM `datathon-real-x16-x20-l4` (L4, id k8hzq9isv) — **currently RUNNING, stop it when done** (`brev stop k8hzq9isv`)
Reachable only via the relay, proxied through the L40S:
`brev mint-cert --env k8hzq9isv --port nport-3KqUUpTqbw5h9Hq5r30dj0tXDNZ --linux-user ubuntu --out-key ~/.brev/ssh-certs/k8hzq9isv`
then ssh `ubuntu@global.prd.ga.run.brev.nvidia.com -p 26656 -i ~/.brev/ssh-certs/k8hzq9isv -J shadeform@216.81.200.24`.
Contains (no rule code anywhere):
- **Accepted YOLO weights** (sha256 match `backend/app/pipeline/yolo.py` ACCEPTED):
  `~/yolo-expanded182-v5/result-person/weights/last.pt`, `~/yolo-expanded182-v5/result-specialist/weights/last.pt`.
  Copy them to the L40S and write the `datathon-detector-ensemble-v1` manifest for `YOLO_CONFIG`
  (see `yolo.py`; needs `class_map`, `nms_iou`, `imgsz`; training args/yaml are next to the weights).
  `pip install ultralytics==8.3.227` in the L40S venv. YOLO runs on CPU in `yolo.py` — switch to cuda if slow.
- Real case clips `~/qwen-real-test/inputs/clips/X16.mov`, `X20.mov` and a prior Qwen+YOLO run
  (`results/results.json`): e.g. X20 "Person-1 opens cabinet, retrieves a cash stack, places it on the desk".

### Teammate branch (conflict)
`origin/codex/inference-backend` @ `ea8d9b4` (by cloudlybtw) implements the opposite topology:
Cloudflare **pushes** to a public GPU FastAPI (`INFERENCE_API_URL`/`INFERENCE_API_KEY`), plus
`qwen_runtime.py`, `tracks.py`, `qwen_contract.py`. It conflicts with our `worker/src/main.py`
and its migration is also numbered `0002`. It is **not** deployed. Its GPU service would need a
public HTTPS endpoint (tunnel) on the Brev box. Recommendation: keep the live pull design;
cherry-pick their YOLO/track code if useful. Don't merge blindly.

## 3. Why violations are always empty

1. `InferenceDetector.analyze_video` (`backend/app/pipeline/inference.py`) always returns `[]`.
2. The deterministic engine (`backend/app/cashrules/engine.py` + `rules/cashrules-catalog.json`,
   25 sequence rules) only trusts human-reviewed evidence (`EvidenceIndex.trusted`), needs
   transactions (pipeline sends `transactions: []`), and expects event types
   (`customer_absent`, `cash_receipt`, `workstation_exit`, …) that Qwen's prompt never emits.
3. Qwen's prompt (`prompt-v2.txt`) forbids alleging violations and allows only 12 object-action types.
4. `jobs.complete` copies the engine decision (`insufficient_evidence`) into `verdict`.
5. All rules in `rules/registry.json` are `verified: false`, so `Rulebook.references()` attaches none
   unless `ALLOW_UNVERIFIED_RULES=true`.

### The team's rules package (checked 2026-10-18)
`~/Downloads/Telegram Desktop/rules_stage/` (+ `VIOLATION_REPORT.html`, `rule_catalog.json`) is the
team's deterministic rules deliverable. **It is already in the repo**: `cashrules/*.py` are
byte-identical to `backend/app/cashrules/`, `rule_catalog.json` == `rules/cashrules-catalog.json`.
Only its CLI (`cashrules/__main__.py`), `examples/`, `reports/` and 98 unit tests aren't copied.
Its own status: replaying all 40 annotated training videos gives **0 violations, all
`insufficient_evidence`**, by design. Every enabled rule (13 of 25) needs reviewed evidence plus
`required_context_facts` (staff roles, room maps, transaction records…) that video alone never
supplies, and 3 of the most video-relevant rules (`DRAFT-231-20` counting without client,
`DRAFT-231-31`, `DRAFT-231-20-STORAGE` prior cash exposed) are `enabled: false`.
So this engine alone cannot produce jury verdicts. Use its catalog for **legal references and
wording** (paragraphs, URLs, applicability notes), and add a separate jury-mode decision layer
(§4.2) whose findings cite the same paragraphs with neutral wording. Don't spend time trying to
satisfy its context gates.

## 4. Plan to ship (in order — do 4.1–4.5 first, deploy, then improve)

The user's intended design: **Qwen describes what happens over time; deterministic code decides.**
Fastest way to get there without fighting the evidence-gated engine:

### 4.1 Qwen: per-window state/action checklist (new prompt, `backend/qwen_runner/`)
Add a second prompt (keep prompt-v2 pass for the dashboard, or drop it if time is short — 5-min limit).
For each window (frames + timestamps), Qwen answers a fixed JSON checklist, each item
`{"value": true|false|null, "frame_ref": "<supplied ref or null>", "note": "short Russian fact"}`:

| key | meaning |
|---|---|
| `customer_present` | a client stands at the counter/window |
| `cashier_at_workstation` | cashier seated/standing at the desk |
| `cash_on_desk` | banknotes/stacks visible on the desk outside a closed device |
| `cash_handover_in` / `cash_handover_out` | cash passes client→cashier / cashier→client |
| `cash_counting` | cashier counts notes (hands or machine) |
| `counter_reject_used` | note from counter's reject pocket put back without detector check |
| `safe_open` / `key_in_safe` | safe/cabinet door open / key left in lock |
| `door_open` | cash-room door open |
| `extra_person_in_cash_area` | someone other than cashier inside the cashier zone (not the client at the window) |
| `personal_items_on_desk` | coat, handbag, drink, food, personal phone on the cash desk |
| `bag_opened` / `bag_cash_to_safe` | collection bag opened / its cash moved to safe |
| `id_document_checked` | client document handled/checked during a cash receipt |
| `journal_stamps_on_desk` | journal/stamps left on desk |
| `screen_unlocked_cashier_away` | workstation screen on/unlocked with cashier away |

Validate like `windows.validate_event` (frame_ref must be supplied, JSON strict). Batch 4 windows;
~8 s per batch on the L40S. Use more, shorter windows (e.g. 3–4 s, 6 frames, max ~30 windows)
— timecode precision is scored. Keep total under ~200 s for a 145 s video.

### 4.2 Deterministic rules over the timeline (new `backend/app/pipeline/violations.py`)
Build a per-window timeline of the checklist, smooth (require a state in ≥2 consecutive
windows unless noted), then emit `CandidateEvent(event_type=<vocabulary id>, start, end,
representative_sec=<frame_ref timestamp>, confidence, observed_facts=[Qwen notes])`:

| vocabulary id | rule (sketch) | legal ref (registry id) |
|---|---|---|
| `safe_left_open` | `safe_open` ≥2 windows while cashier works at desk | nbrk231_p20 |
| `safe_key_left_in_lock` | `key_in_safe` and cashier away from safe | nbrk231_p76_80 |
| `cash_left_unattended` | `cash_on_desk` and not `cashier_at_workstation` ≥2 windows | nbrk231_p20 (+p22) |
| `previous_client_cash_exposed` | cash received from client A, A leaves, client B arrives, `cash_on_desk` still | nbrk231_p20 |
| `incoming_cash_counted_without_client` | `cash_handover_in` then `cash_counting` while `customer_present` false | nbrk231_p19_2, p20 |
| `cash_returned_after_count` | `cash_handover_in`, `cash_counting`, then `cash_handover_out` to same client | nbrk231_p19_2, p20 |
| `second_client_before_first_done` | two clients served overlapping (needs person count; YOLO helps) | nbrk231_p20/p22 |
| `personal_items_at_workplace` | `personal_items_on_desk` ≥2 windows | nbrk231_p12 |
| `unauthorized_person_in_cash_room` | `extra_person_in_cash_area` | nbrk231_p75, nbrk14_access |
| `cash_room_door_left_open` | `door_open` ≥2 windows | nbrk14_access |
| `bag_cash_stored_uncounted` | `bag_opened` then `bag_cash_to_safe` with no `cash_counting` between | nbrk231_p24, p42 |
| `rejected_banknote_unchecked` | `counter_reject_used` | nbrk231_p19_2, p41 |
| `cash_accepted_without_id_check` | `cash_handover_in` with no `id_document_checked` in the service | (see vocabulary) |
| `workstation_left_unlocked` | `screen_unlocked_cashier_away` | (internal policy — label as such) |
| `journal_stamps_left_unattended` | `journal_stamps_on_desk` and cashier away | (see vocabulary) |

Bias towards recall ("пропуск недопустим") but require a cited frame for every finding.

### 4.3 Hook into the pipeline
- `InferenceDetector.analyze_video` → return the `CandidateEvent`s instead of `[]`.
- In `Pipeline.analyze` (`backend/app/pipeline/run.py`) don't `return` early for
  `analyze_video`; fall through to `consolidate()` + evidence extraction + `rulebook.references()`
  + explanation building (already implemented for the non-Qwen path). Evidence file names
  `e1-0044.200.jpg` match the Worker's `EVIDENCE_NAME` regex. Keep `last_analysis` for the dashboard.
- Provider contract `providers.py` rejects extra response keys — extend it for the checklist
  (or return checklist items as events with `data`), keep validation strict.

### 4.4 Verdict + rules
- `jobs.complete`: `verdict = "violation" if findings else "no_violation"`; keep the engine's
  decision inside `analysis.rule_evaluation`. Update tests asserting `insufficient_evidence`
  (`tests/test_inference.py`, `tests/test_qwen_runner.py`, `tests/test_api.py`).
- Set `ALLOW_UNVERIFIED_RULES=true` in `/ephemeral/kc/analyzer.env` **or** verify provisions in
  `rules/registry.json` against `docs/regulatory-research.md` and set `verified: true`. Otherwise
  no rule reference is attached (15 points). Descriptions must stay neutral
  ("может указывать на несоответствие п. 20 Постановления НБРК № 231…"), never "нарушил".
- `type` in the jury JSON = Russian label from `vocabulary.json`.

### 4.5 Deploy + test
1. `cd backend && .venv/bin/python -m pytest -q`.
2. rsync to the L40S, restart daemon + analyzer (§2).
3. Worker only needs redeploy if `worker/` changed: `cd worker && uv run pywrangler deploy`
   (frontend first: `cd frontend && NEXT_PUBLIC_MAX_UPLOAD_MB=95 npm run build`).
4. Test with X16/X20 (copy from the L4 VM) through the live jury API:
   `curl -H "X-API-Key: $KEY" -F file=@X20.mov https://datathon26.pognar1121.workers.dev/api/v1/analyze`
   then poll `/api/v1/jobs/{id}`. Expect X20 → `violation` / «Сейф оставлен открытым».
5. Watch `/ephemeral/kc/daemon.log` (per-window status) and `qwen-log/*.json` (raw output).

### 4.6 Then improve
- YOLO on the L40S (weights on the L4 VM, §2) → real `person-N`, `cash_stack-N`, `bag-N` IDs.
- Evaluate on all training clips (ask the user for them; team annotations with timecodes:
  `~/Downloads/fortebank-team-reviews-2026-10-17.json`). Tune windows/thresholds per type.
- Write the deliverable docs (README: architecture, models — Qwen3-VL-8B-Instruct Apache-2.0,
  YOLO, violation list, legal sources from `docs/regulatory-research.md`, limitations).
- Commit and push (ask the user first). Stop the L4 VM.

## 5. Gotchas
- Network at the venue blocks non-standard ports: use port 22 to the L40S; reach the L4 via the L40S.
- The Python Worker has 128 MB memory: uploads/video stream on the JS side (`handle_upload`,
  `handle_video`, `handle_analyzer_media`). Don't read videos into Python there.
- R2 reads via the SDK return `memoryview` → use `as_bytes()`; D1 `batch()` takes a plain Python list.
- `QWEN_COMMAND_JSON` in env files must be single-quoted (`'["/abs/python","-m","qwen_runner.client"]'`).
- The worker spawns a job subprocess (`multiprocessing` spawn) — scripts need `if __name__ == "__main__":`.
- Provider timeout = 0.8 × `JOB_TIMEOUT_SECONDS` (280 → 224 s); daemon budget `QWEN_TIME_BUDGET_S=200`.
- Jury time limit is 5 min end-to-end including upload, download, and evidence upload.
