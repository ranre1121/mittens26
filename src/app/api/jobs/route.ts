import { uploadErrorResponse } from "@/lib/http";
import { createJobFromRequest } from "@/lib/jobs";
import { parseHistoryQuery } from "@/lib/history";
import { listJobHistory } from "@/lib/history-server";

export async function GET(request: Request) {
  const query = parseHistoryQuery(new URL(request.url).searchParams);
  if (!query) return Response.json({ error: "Некорректные параметры поиска" }, { status: 400 });
  try {
    return Response.json(await listJobHistory(query), { headers: { "Cache-Control": "no-store" } });
  } catch {
    return Response.json({ error: "Не удалось загрузить историю проверок" }, { status: 500 });
  }
}

/** Upload from the web UI. Same as /api/v1/analyze, without the API key. */
export async function POST(request: Request) {
  try {
    const row = await createJobFromRequest(request);
    return Response.json({ job_id: row.id }, { status: 202 });
  } catch (err) {
    return uploadErrorResponse(err);
  }
}
