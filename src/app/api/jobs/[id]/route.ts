import { notFound } from "@/lib/http";
import { getJob, toJobView } from "@/lib/jobs";

/** Job state for the web UI, polled while the analysis runs. */
export async function GET(
  _request: Request,
  ctx: RouteContext<"/api/jobs/[id]">,
) {
  const { id } = await ctx.params;
  const row = await getJob(id);
  if (!row) return notFound();
  return Response.json(await toJobView(row));
}
