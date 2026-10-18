/** 44.2 → "00:44.2", 3725 → "1:02:05.0". */
export function formatTimecode(sec: number) {
  const tenths = Math.round(sec * 10);
  const h = Math.floor(tenths / 36000);
  const m = Math.floor((tenths % 36000) / 600);
  const s = ((tenths % 600) / 10).toFixed(1).padStart(4, "0");
  const mm = String(m).padStart(2, "0");
  return h > 0 ? `${h}:${mm}:${s}` : `${mm}:${s}`;
}

export function formatBytes(bytes: number) {
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(0)} КБ`;
  return `${(bytes / 1024 / 1024).toFixed(1)} МБ`;
}
