#!/usr/bin/env node
/**
 * Stand-in for the real analyzer: claims jobs, downloads the video, reports
 * progress and posts a fake result. Use it to exercise the pipeline end to end
 * until the local model service exists; the real one talks the same protocol.
 *
 *   ANALYZER_TOKEN=... node scripts/mock-analyzer.mjs [--url http://localhost:3000] [--clean] [--once]
 *
 * --clean posts "no violation"; otherwise one fake violation. Evidence frames
 * are taken with ffmpeg when it is installed, else scripts/placeholder.jpg.
 */
import { execFile } from "node:child_process";
import { mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import { promisify } from "node:util";

const run = promisify(execFile);
const args = process.argv.slice(2);
const flag = (name) => args.includes(name);
const option = (name, fallback) => {
  const i = args.indexOf(name);
  return i >= 0 ? args[i + 1] : fallback;
};

const BASE = option("--url", process.env.APP_URL ?? "http://localhost:3000");
const TOKEN = process.env.ANALYZER_TOKEN;
if (!TOKEN) {
  console.error("Set ANALYZER_TOKEN (see .dev.vars)");
  process.exit(1);
}
const auth = { authorization: `Bearer ${TOKEN}` };
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function post(p, body) {
  const res = await fetch(`${BASE}${p}`, {
    method: "POST",
    headers: { ...auth, "content-type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (!res.ok && res.status !== 204) {
    throw new Error(`${p}: ${res.status} ${await res.text()}`);
  }
  return res.status === 204 ? null : res.json();
}

async function frameAt(videoPath, sec) {
  try {
    const out = `${videoPath}.${sec}.jpg`;
    await run("ffmpeg", ["-y", "-loglevel", "error", "-ss", String(sec), "-i", videoPath, "-frames:v", "1", "-q:v", "4", out]);
    return (await readFile(out)).toString("base64");
  } catch {
    const placeholder = new URL("./placeholder.jpg", import.meta.url);
    return (await readFile(placeholder)).toString("base64");
  }
}

async function handle(job) {
  console.log(`claimed ${job.job_id} (${job.filename}, attempt ${job.attempt})`);
  const dir = await mkdtemp(path.join(tmpdir(), "mock-analyzer-"));
  try {
    const res = await fetch(job.video_url);
    if (!res.ok) throw new Error(`video download: ${res.status}`);
    const videoPath = path.join(dir, job.filename.replace(/[^\w.-]/g, "_"));
    await writeFile(videoPath, Buffer.from(await res.arrayBuffer()));

    for (const progress of [20, 45, 70, 90]) {
      await sleep(1000);
      await post(`/api/analyzer/jobs/${job.job_id}/progress`, { progress });
    }

    const violations = flag("--clean")
      ? []
      : [
          {
            type: "Тестовое нарушение (mock)",
            timecode_sec: 2.0,
            description:
              "Заглушка mock-analyzer: здесь будет описание того, что видно на кадре и почему это может быть нарушением.",
            evidence_frames: [
              { timestamp_sec: 1.5, image_base64: await frameAt(videoPath, 1.5) },
              { timestamp_sec: 2.0, image_base64: await frameAt(videoPath, 2.0) },
            ],
            rule: {
              document: "Пример: название нормативного акта НБРК",
              clause: "пункт N",
              url: "https://adilet.zan.kz/",
            },
          },
        ];
    await post(`/api/analyzer/jobs/${job.job_id}/result`, { violations });
    console.log(`done ${job.job_id}: ${violations.length} violation(s)`);
  } catch (err) {
    console.error(`failed ${job.job_id}:`, err.message);
    await post(`/api/analyzer/jobs/${job.job_id}/fail`, { error: err.message }).catch(() => {});
  } finally {
    await rm(dir, { recursive: true, force: true });
  }
}

console.log(`mock analyzer polling ${BASE}`);
for (;;) {
  try {
    const job = await post("/api/analyzer/claim");
    if (job) {
      await handle(job);
      if (flag("--once")) break;
      continue;
    }
  } catch (err) {
    console.error(err.message);
  }
  await sleep(2000);
}
