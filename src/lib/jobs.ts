import "server-only";
import { and, eq, sql } from "drizzle-orm";
import { job, type Job } from "@/db/schema";
import { getDb, getEnv } from "@/lib/env";
import type {
  AnalysisResult,
  JobView,
  PublicJob,
  Verdict,
} from "@/lib/result";

/** Workers reject request bodies over 100 MB; leave room for multipart overhead. */
export const MAX_UPLOAD_BYTES = 95 * 1024 * 1024;
const ALLOWED_EXTENSIONS = [".mov", ".mp4", ".m4v"];

/** A claim with no heartbeat for this long is handed to another analyzer. */
const LEASE_MS = 3 * 60 * 1000;
const MAX_ATTEMPTS = 3;

export class UploadError extends Error {
  constructor(
    message: string,
    readonly status = 400,
  ) {
    super(message);
  }
}

function newJobId() {
  return crypto.randomUUID().replaceAll("-", "").slice(0, 12);
}

/** Validates the multipart `file` field, stores it in R2 and queues a job. */
export async function createJobFromRequest(request: Request): Promise<Job> {
  const declared = Number(request.headers.get("content-length") ?? 0);
  if (declared > MAX_UPLOAD_BYTES) {
    throw new UploadError("File is larger than 95 MB", 413);
  }

  let form: FormData;
  try {
    form = await request.formData();
  } catch {
    throw new UploadError("Expected multipart/form-data with a `file` field");
  }
  const file = form.get("file");
  if (!(file instanceof File)) {
    throw new UploadError("Missing `file` field");
  }
  const name = file.name || "video.mov";
  const ext = name.slice(name.lastIndexOf(".")).toLowerCase();
  if (!ALLOWED_EXTENSIONS.includes(ext)) {
    throw new UploadError(`Unsupported file type ${ext}; upload a .mov video`);
  }
  if (file.size === 0) throw new UploadError("File is empty");
  if (file.size > MAX_UPLOAD_BYTES) {
    throw new UploadError("File is larger than 95 MB", 413);
  }

  const id = newJobId();
  const videoKey = `videos/${id}${ext}`;
  // Clients often send application/octet-stream (curl -F does), so go by extension.
  const contentType = ext === ".mov" ? "video/quicktime" : "video/mp4";

  await getEnv().MEDIA.put(videoKey, file, {
    httpMetadata: { contentType },
    customMetadata: { filename: name },
  });

  const [row] = await getDb()
    .insert(job)
    .values({ id, filename: name, contentType, sizeBytes: file.size, videoKey })
    .returning();
  return row;
}

export async function getJob(id: string) {
  return getDb().query.job.findFirst({ where: eq(job.id, id) });
}

async function loadResult(row: Job): Promise<AnalysisResult | null> {
  if (!row.resultKey) return null;
  const obj = await getEnv().MEDIA.get(row.resultKey);
  return obj ? ((await obj.json()) as AnalysisResult) : null;
}

export async function toPublicJob(row: Job): Promise<PublicJob> {
  if (row.status === "failed") {
    return {
      job_id: row.id,
      status: "failed",
      error: row.error ?? "Analysis failed",
    };
  }
  if (row.status === "done") {
    const result = await loadResult(row);
    if (!result) {
      return { job_id: row.id, status: "failed", error: "Result is missing" };
    }
    return {
      job_id: row.id,
      status: "done",
      verdict: row.verdict ?? "no_violation",
      violations: result.violations,
    };
  }
  return { job_id: row.id, status: "processing" };
}

export async function toJobView(row: Job): Promise<JobView> {
  return {
    ...(await toPublicJob(row)),
    stage: row.status,
    filename: row.filename,
    size_bytes: row.sizeBytes,
    progress: row.progress,
    created_at: row.createdAt.getTime(),
    finished_at: row.finishedAt?.getTime() ?? null,
  };
}

/**
 * Hands the oldest waiting job to an analyzer. A job whose analyzer stopped
 * sending heartbeats is retried, up to MAX_ATTEMPTS, then marked failed.
 */
export async function claimNextJob(): Promise<Job | null> {
  const db = getDb();
  const now = Date.now();
  const staleBefore = now - LEASE_MS;

  await db
    .update(job)
    .set({
      status: "failed",
      error: "Analyzer stopped responding",
      finishedAt: new Date(now),
    })
    .where(
      and(
        eq(job.status, "processing"),
        sql`${job.claimedAt} < ${staleBefore}`,
        sql`${job.attempts} >= ${MAX_ATTEMPTS}`,
      ),
    );

  // One statement, so two analyzers can't claim the same job.
  const rows = await db
    .update(job)
    .set({
      status: "processing",
      claimedAt: new Date(now),
      attempts: sql`${job.attempts} + 1`,
      progress: 0,
    })
    .where(
      eq(
        job.id,
        sql`(select ${job.id} from ${job}
             where ${job.status} = 'queued'
                or (${job.status} = 'processing' and ${job.claimedAt} < ${staleBefore})
             order by ${job.createdAt} limit 1)`,
      ),
    )
    .returning();
  return rows[0] ?? null;
}

/** Heartbeat from the analyzer; also extends its lease. */
export async function reportProgress(id: string, progress: number) {
  const rows = await getDb()
    .update(job)
    .set({
      progress: Math.max(0, Math.min(100, Math.round(progress))),
      claimedAt: new Date(),
    })
    .where(and(eq(job.id, id), eq(job.status, "processing")))
    .returning({ id: job.id });
  return rows.length > 0;
}

export async function completeJob(id: string, result: AnalysisResult) {
  const row = await getJob(id);
  if (!row || row.status !== "processing") return false;

  const resultKey = `results/${id}.json`;
  await getEnv().MEDIA.put(resultKey, JSON.stringify(result), {
    httpMetadata: { contentType: "application/json" },
  });
  // The verdict follows from the violations, so the two can't disagree.
  const verdict: Verdict =
    result.violations.length > 0 ? "violation" : "no_violation";
  await getDb()
    .update(job)
    .set({
      status: "done",
      verdict,
      violationCount: result.violations.length,
      resultKey,
      progress: 100,
      finishedAt: new Date(),
    })
    .where(eq(job.id, id));
  return true;
}

export async function failJob(id: string, error: string) {
  const rows = await getDb()
    .update(job)
    .set({ status: "failed", error, finishedAt: new Date() })
    .where(and(eq(job.id, id), eq(job.status, "processing")))
    .returning({ id: job.id });
  return rows.length > 0;
}
