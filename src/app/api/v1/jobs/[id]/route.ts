import { checkApiKey } from "@/lib/auth";
import { notFound } from "@/lib/http";
import { getJob, toPublicJob } from "@/lib/jobs";

/** Jury API: status (processing | done | failed) and, when done, the result. */
export async function GET(
  request: Request,
  ctx: RouteContext<"/api/v1/jobs/[id]">,
) {
  const denied = checkApiKey(request);
  if (denied) return denied;

  const { id } = await ctx.params;
  const row = await getJob(id);
  if (!row) return notFound();
  return Response.json(await toPublicJob(row));
}
