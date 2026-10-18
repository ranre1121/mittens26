import "server-only";
import { and, count, desc, eq, or, sql } from "drizzle-orm";
import { job } from "@/db/schema";
import { getDb } from "@/lib/env";
import type { HistoryQuery, HistoryResponse } from "@/lib/history";

const PAGE_SIZE = 20;

export async function listJobHistory(query: HistoryQuery): Promise<HistoryResponse> {
  const db = getDb();
  // Treat SQL wildcard characters as part of the filename being searched.
  const pattern = `%${query.q.replace(/[\\%_]/g, "\\$&")}%`;
  const escape = "\\";
  const where = and(
    query.status === "all" ? undefined : eq(job.status, query.status),
    query.q ? or(sql`${job.filename} like ${pattern} escape ${escape}`, sql`${job.id} like ${pattern} escape ${escape}`) : undefined,
  );
  const [summary] = await db.select({ total: count() }).from(job).where(where);
  const total = summary.total;
  const pageCount = Math.max(1, Math.ceil(total / PAGE_SIZE));
  const page = Math.min(query.page, pageCount);
  const rows = await db.select({
    id: job.id,
    filename: job.filename,
    sizeBytes: job.sizeBytes,
    status: job.status,
    progress: job.progress,
    verdict: job.verdict,
    violationCount: job.violationCount,
    createdAt: job.createdAt,
    finishedAt: job.finishedAt,
  }).from(job).where(where).orderBy(desc(job.createdAt), desc(job.id)).limit(PAGE_SIZE).offset((page - 1) * PAGE_SIZE);
  return {
    items: rows.map((row) => ({ ...row, createdAt: row.createdAt.getTime(), finishedAt: row.finishedAt?.getTime() ?? null })),
    total, page, pageCount, pageSize: PAGE_SIZE,
  };
}
