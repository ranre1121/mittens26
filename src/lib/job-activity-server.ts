import "server-only";
import { and, asc, eq, gt, or } from "drizzle-orm";
import { job } from "@/db/schema";
import { getDb } from "@/lib/env";
import { parseActivityCursor, type JobActivity } from "@/lib/job-activity";

const BATCH_SIZE = 50;

/** Completion order is independent of upload order, including old queued videos. */
export async function listJobCompletions(cursor: string | null): Promise<JobActivity> {
  if (cursor === null) return { items: [], cursor: `${Date.now()}.`, hasMore: false };
  const after = parseActivityCursor(cursor);
  if (!after) throw new Error("Invalid activity cursor");
  const rows = await getDb().select({ id: job.id, filename: job.filename, finishedAt: job.finishedAt })
    .from(job).where(and(eq(job.status, "done"), or(
      gt(job.finishedAt, new Date(after.time)),
      and(eq(job.finishedAt, new Date(after.time)), gt(job.id, after.id)),
    ))).orderBy(asc(job.finishedAt), asc(job.id)).limit(BATCH_SIZE);
  const items = rows.map((row) => ({ ...row, finishedAt: row.finishedAt!.getTime() }));
  const last = items.at(-1);
  return { items, cursor: last ? `${last.finishedAt}.${last.id}` : cursor, hasMore: items.length === BATCH_SIZE };
}
