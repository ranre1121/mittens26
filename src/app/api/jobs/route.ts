import { uploadErrorResponse } from "@/lib/http";
import { createJobFromRequest } from "@/lib/jobs";

/** Upload from the web UI. Same as /api/v1/analyze, without the API key. */
export async function POST(request: Request) {
  try {
    const row = await createJobFromRequest(request);
    return Response.json({ job_id: row.id }, { status: 202 });
  } catch (err) {
    return uploadErrorResponse(err);
  }
}
