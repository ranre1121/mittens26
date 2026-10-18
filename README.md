# Контроль кассы — Datathon 26 (ForteBank)

A web service that checks cashier videos for visible signs of cash-discipline
violations. Upload a `.mov` and you get a verdict, timecodes, evidence frames
and the regulation each finding relates to. There is no login: the UI opens
straight to the upload screen.

**Next.js 16** (App Router, frontend and API) on **Cloudflare Workers** via
[OpenNext](https://opennext.js.org/cloudflare), with **D1** for job state and
**R2** for videos and results.

## Architecture

```
browser / jury ──► Worker (Next.js) ──► D1  job rows: status, verdict, progress
                        │          └──► R2  videos/<id>.mov, results/<id>.json
                        ▲
                        │ claim → download → progress → result
                   analyzer (local models, separate process)
```

The Worker never runs models. The case requires all models to run inside the
team's own service, with no cloud AI APIs (section 7), and Workers can't run
video models anyway. The **analyzer** is a separate process (a GPU box, a
laptop, or another host) that pulls jobs over HTTP. **No real analyzer is included
yet**; `npm run mock-analyzer` only exercises the protocol. It marks its output
as a demonstration and does not claim to detect actions or validate rules.

The case also requires offline operation after model weights are downloaded.
The current production Cloudflare D1/R2 architecture does not establish that
guarantee; a local storage/runtime deployment and a real local analyzer remain
necessary before claiming full compliance. Development uses local emulation.

### Routes

| Route                                 | Auth                   | Purpose                                                  |
| ------------------------------------- | ---------------------- | -------------------------------------------------------- |
| `/`                                   | —                      | Upload page                                              |
| `/history`                            | —                      | Searchable check history, status filters, live updates    |
| `/jobs/<id>`                          | —                      | Live status, verdict, violations, player that seeks       |
| `POST /api/v1/analyze`                | `X-API-Key`            | Jury API: multipart `file` → `{ job_id }`                 |
| `GET /api/v1/jobs/<id>`               | `X-API-Key`            | Jury API: `processing` / `done` / `failed` + result       |
| `POST /api/jobs`, `GET /api/jobs/<id>` | —                      | Same as above for the UI, plus filename/progress          |
| `GET /api/jobs`                       | —                      | History metadata: `q`, `status`, `page`; 20 records/page  |
| `GET /api/jobs/activity`              | —                      | Completion notifications with a timestamp/ID cursor      |
| `GET /api/jobs/<id>/video`            | —                      | Video stream from R2, with Range support                  |
| `POST /api/analyzer/claim`            | `Bearer ANALYZER_TOKEN` | Next queued job (`204` when empty)                        |
| `POST /api/analyzer/jobs/<id>/progress` | `Bearer ANALYZER_TOKEN` | `{ "progress": 0–100 }`, also a heartbeat                |
| `POST /api/analyzer/jobs/<id>/result` | `Bearer ANALYZER_TOKEN` | `{ "violations": [...] }`                                |
| `POST /api/analyzer/jobs/<id>/fail`   | `Bearer ANALYZER_TOKEN` | `{ "error": "..." }`                                     |

### History

The history page reads existing D1 job records, newest first, with a stable ID
tie-breaker. It refreshes every five seconds while visible and when returning to
the tab. Search matches filenames and job IDs; `%` and `_` are literal characters.
Filters and pagination run on the server. The list/card switch keeps the same
records and filters. Video previews are loaded near the viewport, use metadata
and a short seek, and never autoplay. Dates use the Asia/Almaty time zone.
Numbered pagination follows Watermelon `pagination-9`: a flat active underline,
previous/next actions and compact page ranges.

The persistent layout polls `/api/jobs/activity` every five seconds while visible.
It starts with a server-time cursor and announces only subsequent completions,
including old uploads that finish while new videos are being sent. Cursor pages
use completion time plus ID, independently of history ordering. The Watermelon
`announcement-1` banner offers the result link and dismissal. During the upload
wave it is shown inside the overlay; navigation becomes available after sending.
This is an in-app notification, not a browser push or background service.

### Analyzer protocol

1. `POST /api/analyzer/claim` → `{ job_id, filename, video_url, attempt, ... }`.
2. Download `video_url`.
3. Send `progress` at least every 3 minutes. A claim with no heartbeat for
   3 minutes goes back to the queue; after 3 attempts the job is marked failed.
4. Post the `result`, or `fail`.

Result body (validated in `src/lib/result.ts`):

```json
{
  "violations": [
    {
      "type": "Название типа нарушения",
      "timecode_sec": 44.2,
      "description": "Краткое описание возможного нарушения",
      "explanation": {
        "observation": "Только наблюдаемое действие",
        "reasoning": "Почему действие может указывать на нарушение"
      },
      "evidence_frames": [{ "timestamp_sec": 44.2, "image_base64": "/9j/4AAQ..." }],
      "rule": { "document": "Название проверенного документа", "clause": "Точный пункт или раздел", "url": "https://example.org/verified-document" }
    }
  ],
  "analysis": {
    "checked_types": ["Тип события, который действительно проверялся"],
    "limitations": ["Ограничение, выявленное для этой записи"],
    "unassessed_segments": [
      { "start_sec": 60, "end_sec": 65, "reason": "Причина невозможности оценки" }
    ]
  }
}
```

This is a format example, not a real finding or regulatory citation. The analyzer
must supply verified document titles, clauses and HTTP(S) links. Never invent
missing references.

The server derives `verdict` from the list: any violations means `violation`,
none means `no_violation`. `evidence_frames` must hold 1–3 JPEGs. `rule` is
optional; the UI shows it as the regulatory reference, and the jury API passes
it through as an extra field. `explanation` and `analysis` are optional, so old
analyzers remain compatible. Analysis context is stored with the result and
returned by the UI endpoint; the jury endpoint keeps its existing top-level
shape. Missing sources and coverage are explicitly shown as missing.

### Result screen

The dashboard has a summary and metrics on the left, the source video and event
navigation on the right, event distribution and coverage below, and a full-width
evidence section. It has no sidebar. Counts come from actual returned events and
frames; elapsed time includes the queue. No confidence or risk scores are invented.

Each event shows its type, timecode, description, observation/reasoning, 1–3 JPEG
frames with their own times, and document/section/link. Timecodes seek the player;
frames open in a keyboard-accessible dialog with an action to seek that frame.
Both exact case verdicts, empty results, limitations and demo output are covered.
The primary “Проверить другое видео” link uses the shifting highlight from
Watermelon `button-10`, adapted to violet metal. These three registry components
are adapted locally using the existing React/CSS Modules stack, without a second
button library or unused icon dependencies.

## Local development

```bash
npm install
cp .dev.vars.example .dev.vars     # fill in API_KEYS and ANALYZER_TOKEN
npm run db:migrate:local
npm run dev                        # http://localhost:3000

# in another terminal: process uploads with the stand-in analyzer
ANALYZER_TOKEN=<from .dev.vars> npm run mock-analyzer -- --url http://localhost:3000
```

`npm run preview` builds the Worker and runs it in workerd. This is the closest
local match to production.

## Deploying

One-time setup (D1 `datathon26-db` already exists):

```bash
npx wrangler r2 bucket create datathon26-videos   # R2 must be enabled in the dashboard first
npm run db:migrate:remote
npx wrangler secret put API_KEYS                  # key(s) for the jury, comma-separated
npx wrangler secret put ANALYZER_TOKEN
```

Then deploy with `npm run deploy`.

## Limits

- Uploads are capped at 95 MB, because Workers reject request bodies over 100 MB.
  The case videos are about 29 MB.
- `.mov` is served as `video/mp4` so browsers play H.264 QuickTime files.
- The jury counts a video as unprocessed after 5 minutes. Keep at least one
  analyzer running and polling.
