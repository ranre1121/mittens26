export function jobHref(id: string, batch: string[] = []) {
  const path = `/jobs/${encodeURIComponent(id)}`;
  return batch.length > 1 ? `${path}?batch=${encodeURIComponent(batch.join(","))}` : path;
}
