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
laptop, or a Cloudflare Container) that pulls jobs over HTTP. Until it exists,
`npm run mock-analyzer` stands in for it.

### Routes

| Route                                 | Auth                   | Purpose                                                  |
| ------------------------------------- | ---------------------- | -------------------------------------------------------- |
| `/`                                   | —                      | Upload page                                              |
| `/jobs/<id>`                          | —                      | Live status, verdict, violations, player that seeks       |
| `POST /api/v1/analyze`                | `X-API-Key`            | Jury API: multipart `file` → `{ job_id }`                 |
| `GET /api/v1/jobs/<id>`               | `X-API-Key`            | Jury API: `processing` / `done` / `failed` + result       |
| `POST /api/jobs`, `GET /api/jobs/<id>` | —                      | Same as above for the UI, plus filename/progress          |
| `GET /api/jobs/<id>/video`            | —                      | Video stream from R2, with Range support                  |
| `POST /api/analyzer/claim`            | `Bearer ANALYZER_TOKEN` | Next queued job (`204` when empty)                        |
| `POST /api/analyzer/jobs/<id>/progress` | `Bearer ANALYZER_TOKEN` | `{ "progress": 0–100 }`, also a heartbeat                |
| `POST /api/analyzer/jobs/<id>/result` | `Bearer ANALYZER_TOKEN` | `{ "violations": [...] }`                                |
| `POST /api/analyzer/jobs/<id>/fail`   | `Bearer ANALYZER_TOKEN` | `{ "error": "..." }`                                     |

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
      "description": "Что видно на видео и почему это нарушение",
      "evidence_frames": [{ "timestamp_sec": 44.2, "image_base64": "/9j/4AAQ..." }],
      "rule": { "document": "Постановление НБРК № …", "clause": "п. 12", "url": "https://adilet.zan.kz/..." }
    }
  ]
}
```

The server derives `verdict` from the list: any violations means `violation`,
none means `no_violation`. `evidence_frames` must hold 1–3 JPEGs. `rule` is
optional; the UI shows it as the regulatory reference, and the jury API passes
it through as an extra field.

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
