export type JobCompletion = { id: string; filename: string; finishedAt: number };
export type JobActivity = { items: JobCompletion[]; cursor: string; hasMore: boolean };

export function parseActivityCursor(value: string) {
  const match = /^(\d{1,16})\.([a-f0-9]{0,32})$/.exec(value);
  if (!match) return null;
  const time = Number(match[1]);
  return Number.isSafeInteger(time) && time <= 8.64e15 ? { time, id: match[2] } : null;
}
