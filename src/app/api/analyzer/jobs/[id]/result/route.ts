import { z } from "zod";
import { checkAnalyzerToken } from "@/lib/auth";
import { completeJob } from "@/lib/jobs";
import { analysisResultSchema } from "@/lib/result";

/** Final result: `{ "violations": [...] }`; the verdict is derived from it. */
export async function POST(
  request: Request,
  ctx: RouteContext<"/api/analyzer/jobs/[id]/result">,
) {
  const denied = checkAnalyzerToken(request);
  if (denied) return denied;

  const parsed = analysisResultSchema.safeParse(
    await request.json().catch(() => null),
  );
  if (!parsed.success) {
    return Response.json({ error: z.prettifyError(parsed.error) }, { status: 400 });
  }
  const { id } = await ctx.params;
  if (!(await completeJob(id, parsed.data))) {
    return Response.json({ error: "Job is not processing" }, { status: 409 });
  }
  return Response.json({ ok: true });
}
