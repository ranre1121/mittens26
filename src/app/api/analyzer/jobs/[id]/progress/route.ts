import { z } from "zod";
import { checkAnalyzerToken } from "@/lib/auth";
import { reportProgress } from "@/lib/jobs";

const bodySchema = z.object({ progress: z.number().min(0).max(100) });

/** Heartbeat: `{ "progress": 0–100 }`. Send at least every few minutes. */
export async function POST(
  request: Request,
  ctx: RouteContext<"/api/analyzer/jobs/[id]/progress">,
) {
  const denied = checkAnalyzerToken(request);
  if (denied) return denied;

  const parsed = bodySchema.safeParse(await request.json().catch(() => null));
  if (!parsed.success) {
    return Response.json({ error: z.prettifyError(parsed.error) }, { status: 400 });
  }
  const { id } = await ctx.params;
  if (!(await reportProgress(id, parsed.data.progress))) {
    return Response.json({ error: "Job is not processing" }, { status: 409 });
  }
  return Response.json({ ok: true });
}
