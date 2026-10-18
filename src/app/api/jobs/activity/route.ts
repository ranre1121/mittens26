import { parseActivityCursor } from "@/lib/job-activity";
import { listJobCompletions } from "@/lib/job-activity-server";

export async function GET(request: Request) {
  const cursor = new URL(request.url).searchParams.get("after");
  if (cursor !== null && !parseActivityCursor(cursor)) return Response.json({ error: "Некорректный курсор" }, { status: 400 });
  try {
    return Response.json(await listJobCompletions(cursor), { headers: { "Cache-Control": "no-store" } });
  } catch {
    return Response.json({ error: "Не удалось получить обновления" }, { status: 500 });
  }
}
