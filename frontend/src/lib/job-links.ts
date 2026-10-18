/** A query route works for arbitrary job ids in the static export. */
export function jobHref(id: string, batch: string[] = []) {
  const query = new URLSearchParams({ id });
  if (batch.length > 1) query.set("batch", batch.join(","));
  return `/jobs/?${query}`;
}
