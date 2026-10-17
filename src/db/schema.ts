import { sqliteTable, text, integer, index } from "drizzle-orm/sqlite-core";
import { sql } from "drizzle-orm";

/**
 * Lifecycle: queued → processing → done | failed.
 * The public API reports "queued" as "processing" (see toPublicStatus).
 */
export const JOB_STATUSES = ["queued", "processing", "done", "failed"] as const;
export type JobStatus = (typeof JOB_STATUSES)[number];

export const job = sqliteTable(
  "job",
  {
    id: text("id").primaryKey(),
    status: text("status", { enum: JOB_STATUSES }).notNull().default("queued"),
    filename: text("filename").notNull(),
    contentType: text("content_type").notNull(),
    sizeBytes: integer("size_bytes").notNull(),
    // R2 key of the uploaded video.
    videoKey: text("video_key").notNull(),
    // Set once done; the full result (with base64 frames) lives in R2 under resultKey.
    verdict: text("verdict", { enum: ["violation", "no_violation"] }),
    violationCount: integer("violation_count"),
    resultKey: text("result_key"),
    // 0–100, reported by the analyzer while processing.
    progress: integer("progress").notNull().default(0),
    error: text("error"),
    // How many times an analyzer has claimed this job; stale claims are retried.
    attempts: integer("attempts").notNull().default(0),
    createdAt: integer("created_at", { mode: "timestamp_ms" })
      .notNull()
      .default(sql`(cast(unixepoch('subsecond') * 1000 as integer))`),
    claimedAt: integer("claimed_at", { mode: "timestamp_ms" }),
    finishedAt: integer("finished_at", { mode: "timestamp_ms" }),
  },
  (t) => [index("job_status_created_idx").on(t.status, t.createdAt)],
);

export type Job = typeof job.$inferSelect;
