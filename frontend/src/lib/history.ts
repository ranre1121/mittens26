export const HISTORY_STATUSES = [
  { value: "all", label: "Все статусы" },
  { value: "queued", label: "В очереди" },
  { value: "processing", label: "В работе" },
  { value: "done", label: "Завершены" },
  { value: "failed", label: "Ошибка" },
] as const;

export type HistoryStatus = (typeof HISTORY_STATUSES)[number]["value"];
export type HistoryQuery = { q: string; status: HistoryStatus; page: number };
export type HistoryItem = {
  id: string;
  filename: string;
  sizeBytes: number;
  status: Exclude<HistoryStatus, "all">;
  progress: number;
  verdict: "violation" | "no_violation" | null;
  violationCount: number | null;
  createdAt: number;
  finishedAt: number | null;
};
export type HistoryResponse = {
  items: HistoryItem[];
  total: number;
  page: number;
  pageSize: number;
  pageCount: number;
};

export function parseHistoryQuery(params: URLSearchParams): HistoryQuery | null {
  const status = params.get("status") ?? "all";
  const page = params.get("page") ?? "1";
  const q = (params.get("q") ?? "").trim();
  if (!HISTORY_STATUSES.some((item) => item.value === status) || !/^[1-9]\d{0,6}$/.test(page) || q.length > 120) return null;
  return { q, status: status as HistoryStatus, page: Number(page) };
}
