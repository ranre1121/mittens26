import { checkApiKey } from "@/lib/auth";
import { uploadErrorResponse } from "@/lib/http";
import { createJobFromRequest } from "@/lib/jobs";

/** Jury API: upload a .mov as the multipart `file` field, get a job_id back. */
export async function POST(request: Request) {
  const denied = checkApiKey(request);
  if (denied) return denied;

  try {
    const row = await createJobFromRequest(request);
    return Response.json(
      { job_id: row.id, status: "processing" },
      { status: 202 },
    );
  } catch (err) {
    return uploadErrorResponse(err);
  }
}
