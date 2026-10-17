import { checkAnalyzerToken } from "@/lib/auth";
import { claimNextJob } from "@/lib/jobs";

/**
 * The analyzer (the local model service) polls this for work.
 * 200 with the job, or 204 when the queue is empty.
 */
export async function POST(request: Request) {
  const denied = checkAnalyzerToken(request);
  if (denied) return denied;

  const row = await claimNextJob();
  if (!row) return new Response(null, { status: 204 });

  const origin = new URL(request.url).origin;
  return Response.json({
    job_id: row.id,
    filename: row.filename,
    content_type: row.contentType,
    size_bytes: row.sizeBytes,
    attempt: row.attempts,
    video_url: `${origin}/api/jobs/${row.id}/video`,
  });
}
