import { z } from "zod";
import { checkAnalyzerToken } from "@/lib/auth";
import { failJob } from "@/lib/jobs";

const bodySchema = z.object({ error: z.string().min(1).max(2000) });

/** The analyzer could not process the video: `{ "error": "..." }`. */
export async function POST(
  request: Request,
  ctx: RouteContext<"/api/analyzer/jobs/[id]/fail">,
) {
  const denied = checkAnalyzerToken(request);
  if (denied) return denied;

  const parsed = bodySchema.safeParse(await request.json().catch(() => null));
  if (!parsed.success) {
    return Response.json({ error: z.prettifyError(parsed.error) }, { status: 400 });
  }
  const { id } = await ctx.params;
  if (!(await failJob(id, parsed.data.error))) {
    return Response.json({ error: "Job is not processing" }, { status: 409 });
  }
  return Response.json({ ok: true });
}
