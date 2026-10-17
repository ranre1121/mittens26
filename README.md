# Контроль кассы: Datathon 26 (ForteBank)

A service that checks silent cashier videos for visible signs of possible
cash-discipline violations. It returns event types, timecodes, evidence frames
and regulatory references for a human to review. The web interface and the
jury API use the same pipeline.

This follows `ARCHITECTURE.md`, a modular monolith on one host:

```
browser / jury ──► FastAPI (api)  ──► SQLite  data/app.sqlite3   (jobs, events)
                     │  static frontend   └──► files  data/uploads, results, evidence
                     ▼
               analysis worker (separate process, claims queued jobs)
                     └──► pipeline ──► local model weights (models/) + rule registry (rules/)
```

- **API** (`backend/app/api.py`): validates and stores uploads, creates
  queued jobs, and serves results and the static frontend. It never runs
  inference.
- **Worker** (`backend/app/worker.py`): loads models once, claims one job at a
  time, runs the pipeline, saves artifacts, and only then marks the job done.
- **Frontend** (`frontend/`): Next.js built as a static export (`frontend/out`),
  served by FastAPI from the same origin.

## Repository layout

```
backend/
  app/
    api.py          HTTP routes, upload validation, jury and interface views
    worker.py       job loop, restart recovery
    jobs.py         job records: atomic claim, complete, fail, requeue
    db.py           SQLite (WAL, busy timeout, short transactions)
    storage.py      file layout under data/
    video.py        PyAV decoding with source presentation timestamps
    rules.py        event vocabulary + rule registry
    pipeline/       sampling → detection → consolidation → evidence → rules → explanation
  tests/
frontend/           Next.js (static export)
rules/
  vocabulary.json   event IDs from the team annotations (stable IDs, labels, explanations)
  registry.json     candidate rule references; all start unverified
models/             local model weights (not in git)
data/               runtime data (not in git)
```

## Running locally

Backend (Python 3.11+):

```bash
cd backend
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"
.venv/bin/pytest
```

Run the API and the worker in two terminals. Both read the same environment
variables (see `.env.example`):

```bash
cd backend && API_KEYS=dev-key .venv/bin/uvicorn app.api:app --port 8000
```

```bash
cd backend && STUB_MODE=demo .venv/bin/python -m app.worker
```

Frontend, either way:

- **Through FastAPI:** run `cd frontend && npm install && npm run build`, then
  open http://localhost:8000. FastAPI serves `frontend/out`.
- **With hot reload:** run `cd frontend && npm run dev`, then open
  http://localhost:3000. `next dev` proxies `/api` to port 8000.

## Deploying (one host)

```bash
cp .env.example .env      # set API_KEYS
docker compose up -d --build
```

This starts `api` (port 8000) and `worker` from one image, sharing `./data`.
Put an HTTPS reverse proxy in front of port 8000 for the public URL. Once a GPU
model is integrated, switch the image base to CUDA and uncomment the GPU block
in `docker-compose.yml`. Verify that analysis works with networking disabled.

## API

### Jury API (`X-API-Key` required)

| Method | Path | |
|---|---|---|
| `POST` | `/api/v1/analyze` | multipart `file` (.mov) → `{"job_id": "…"}` |
| `GET` | `/api/v1/jobs/{job_id}` | `processing` / `failed` / `done` with the case schema |

The response contains exactly the fields from section 10 of the case. That
schema has no rule field, so the regulatory reference is appended to
`description` ("Норматив: …").

### Interface API (no key)

The interface has no login, so these routes need no key.

| Method | Path | |
|---|---|---|
| `POST` | `/api/jobs` | same upload as `/api/v1/analyze` |
| `GET` | `/api/jobs/{job_id}` | state, stage, progress, queue position, structured rules, evidence URLs |
| `GET` | `/api/jobs/{job_id}/video` | uploaded video, supports Range requests |
| `GET` | `/api/jobs/{job_id}/evidence/{name}` | evidence JPEG |

## Job lifecycle

`queued → running → done | failed`. The public status reports `queued` and
`running` as `processing`.

- **Uploads:** each upload streams into `data/tmp`, gets probed with PyAV, and
  only then moves to `data/uploads/<job_id>/`. Partial or invalid uploads never
  become jobs.
- **Claiming:** the worker claims jobs atomically (`BEGIN IMMEDIATE`) and
  records the attempt count and stage.
- **Completing:** `data/results/<job_id>/result.json` and the evidence JPEGs
  are written before the job is marked `done`. A retry deletes the previous
  attempt's artifacts first.
- **Restart recovery:** when the worker starts, it requeues jobs left
  `running`. A job that has already used `MAX_ATTEMPTS` is marked failed
  instead.
- **Failures:** a processing failure is reported as `failed`, never as
  `no_violation`.

## Pipeline status

`backend/app/pipeline/` has the full stage structure. Detection is still a
stub (`StubDetector`):

- `STUB_MODE=none` reports no events.
- `STUB_MODE=demo` reports one synthetic event per video to exercise
  evidence, rules and output. Never use it for judging.

To integrate a model, implement the `Detector` protocol in
`pipeline/detectors.py`. Load weights from `MODELS_DIR` in `__init__`, return
`CandidateEvent`s whose `event_type` is an ID from `rules/vocabulary.json`,
and return it from `build_detector()`.

## Rule references

Every entry in `rules/registry.json` starts with `verified: false`, and
unverified rules are not cited. Until someone checks a provision on
adilet.zan.kz and sets `verified: true`, descriptions say the reference awaits
verification. `ALLOW_UNVERIFIED_RULES=true` cites them anyway, marked
"[не проверено]".
