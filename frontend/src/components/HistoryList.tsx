"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { HISTORY_STATUSES, type HistoryItem, type HistoryQuery, type HistoryResponse, type HistoryStatus } from "@/lib/history";
import { formatBytes } from "@/lib/format";
import { jobHref } from "@/lib/job-links";
import { HistoryVideoPreview } from "@/components/HistoryVideoPreview";
import Pagination9 from "@/components/watermelon/pagination-9";
import styles from "@/components/review/review.module.css";
import { apiFetch } from "@/lib/session";

const dateFormat = new Intl.DateTimeFormat("ru-RU", { day: "2-digit", month: "short", year: "numeric", timeZone: "Asia/Almaty" });
const timeFormat = new Intl.DateTimeFormat("ru-RU", { hour: "2-digit", minute: "2-digit", timeZone: "Asia/Almaty" });
const dayKeyFormat = new Intl.DateTimeFormat("en-CA", { year: "numeric", month: "2-digit", day: "2-digit", timeZone: "Asia/Almaty" });
const dayLabelFormat = new Intl.DateTimeFormat("ru-RU", { day: "numeric", month: "long", year: "numeric", timeZone: "Asia/Almaty" });
const statusLabels = { queued: "В очереди", processing: "В работе", done: "Завершена", failed: "Ошибка" };

type Snapshot = { key: string; revision: number; data: HistoryResponse | null; error: string | null };

function useHistory(query: HistoryQuery, revision: number) {
  const key = new URLSearchParams({ q: query.q.trim(), status: query.status, page: String(query.page) }).toString();
  const [snapshot, setSnapshot] = useState<Snapshot | null>(null);

  useEffect(() => {
    let disposed = false;
    let timer: ReturnType<typeof setTimeout>;
    let request: AbortController | null = null;

    async function load() {
      clearTimeout(timer);
      if (document.visibilityState === "hidden" || disposed) return;
      request?.abort();
      const current = new AbortController();
      request = current;
      try {
        const response = await apiFetch(`/api/jobs?${key}`, { cache: "no-store", signal: current.signal });
        if (!response.ok) throw new Error("Не удалось загрузить историю. Попробуйте ещё раз.");
        const data = await response.json() as HistoryResponse;
        if (!disposed && !current.signal.aborted) setSnapshot({ key, revision, data, error: null });
      } catch {
        if (!disposed && !current.signal.aborted) setSnapshot((previous) => ({
          key, revision, data: previous?.key === key ? previous.data : null,
          error: "Не удалось обновить историю. Проверьте соединение и попробуйте ещё раз.",
        }));
      } finally {
        if (!disposed && request === current && !current.signal.aborted) timer = setTimeout(load, 5000);
      }
    }

    timer = setTimeout(load, query.q ? 250 : 0);
    window.addEventListener("focus", load);
    document.addEventListener("visibilitychange", load);
    return () => {
      disposed = true;
      clearTimeout(timer);
      request?.abort();
      window.removeEventListener("focus", load);
      document.removeEventListener("visibilitychange", load);
    };
  }, [key, query.q, revision]);

  const current = snapshot?.key === key ? snapshot : null;
  const previousQuery = new URLSearchParams(snapshot?.key);
  const sameFilters = previousQuery.get("q") === query.q.trim() && previousQuery.get("status") === query.status;
  return { data: current?.data ?? null, pagination: current?.data ?? (sameFilters ? snapshot?.data : null), error: current?.error ?? null, pending: !current || current.revision !== revision };
}

function ResultLabel({ item }: { item: HistoryItem }) {
  if (item.status === "failed") return <span className={styles.muted}>Анализ не выполнен</span>;
  if (item.status !== "done") return <span className={styles.muted}>Результат ожидается</span>;
  if (item.verdict === "violation") return <span className={styles.findingLabel}>Отмечено событий: {item.violationCount ?? "—"}</span>;
  if (item.verdict === "no_violation") return <span>Нарушений не обнаружено</span>;
  return <span className={styles.muted}>Откройте проверку</span>;
}

function groupByDay(items: HistoryItem[]) {
  const today = dayKeyFormat.format(Date.now());
  const yesterday = dayKeyFormat.format(Date.now() - 86_400_000);
  const groups = new Map<string, { label: string; items: HistoryItem[] }>();
  for (const item of items) {
    const key = dayKeyFormat.format(item.createdAt);
    if (!groups.has(key)) groups.set(key, { label: key === today ? "Сегодня" : key === yesterday ? "Вчера" : dayLabelFormat.format(item.createdAt), items: [] });
    groups.get(key)!.items.push(item);
  }
  return [...groups.entries()];
}

function HistoryVideo({ item }: { item: HistoryItem }) {
  return (
    <li>
      <Link href={jobHref(item.id)} className={styles.historyVideo} aria-label={`Открыть проверку ${item.filename}`} prefetch={false}>
        <HistoryVideoPreview id={item.id} filename={item.filename} />
        <div className={styles.videoInfo}>
          <h3 title={item.filename}>{item.filename}</h3>
          <p className={styles.videoMetadata}><time dateTime={new Date(item.createdAt).toISOString()}>{dateFormat.format(item.createdAt)} · {timeFormat.format(item.createdAt)}</time><span aria-hidden="true">·</span><span>{formatBytes(item.sizeBytes)}</span></p>
          <p className={styles.videoRecordId}>Проверка № {item.id}</p>
          <div className={styles.videoStatus}><span className={styles.status} data-status={item.status}><span aria-hidden="true" className={styles.statusDot} />{statusLabels[item.status]}{item.status === "processing" && ` · ${Math.max(0, Math.min(100, Math.round(item.progress)))}%`}</span></div>
          <p className={styles.videoResult}><ResultLabel item={item} /></p>
        </div>
        <span className={styles.videoOpen} aria-hidden="true">↗</span>
      </Link>
    </li>
  );
}

export function HistoryList() {
  const [query, setQuery] = useState<HistoryQuery>({ q: "", status: "all", page: 1 });
  const [revision, setRevision] = useState(0);
  const [view, setView] = useState<"list" | "grid">("list");
  const { data, pagination, error, pending } = useHistory(query, revision);
  const filtered = Boolean(query.q.trim() || query.status !== "all");
  const reset = () => setQuery({ q: "", status: "all", page: 1 });

  return (
    <section className={styles.historyPanel} aria-label="Список проверок">
      <div className={styles.journalHeading}>
        <div><h2>Все видео</h2><p>Сначала новые · время Алматы</p></div>
        <div className={styles.viewSwitch} data-view={view} role="group" aria-label="Вид истории">
          <button type="button" className={styles.viewButton} aria-pressed={view === "list"} onClick={() => setView("list")}><svg aria-hidden="true" viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.5"><rect x="2" y="3" width="5" height="5" rx="1"/><rect x="2" y="12" width="5" height="5" rx="1"/><path d="M10 4h8M10 7h6M10 13h8M10 16h6"/></svg>Список</button>
          <button type="button" className={styles.viewToggle} role="switch" aria-label="Показывать историю карточками" aria-checked={view === "grid"} onClick={() => setView((current) => current === "list" ? "grid" : "list")}>
            <span className={styles.viewThumb} aria-hidden="true" />
          </button>
          <button type="button" className={styles.viewButton} aria-pressed={view === "grid"} onClick={() => setView("grid")}><svg aria-hidden="true" viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.5"><rect x="2" y="2" width="6" height="6" rx="1"/><rect x="12" y="2" width="6" height="6" rx="1"/><rect x="2" y="12" width="6" height="6" rx="1"/><rect x="12" y="12" width="6" height="6" rx="1"/></svg>Карточки</button>
        </div>
      </div>
      <div className={styles.toolbar}>
        <label className={styles.search}>
          <span className="sr-only">Поиск по имени файла или номеру проверки</span>
          <svg aria-hidden="true" viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.5"><circle cx="8.5" cy="8.5" r="5.5"/><path d="m13 13 4 4"/></svg>
          <input type="search" placeholder="Имя файла или номер проверки" maxLength={120} value={query.q} onChange={(event) => setQuery((current) => ({ ...current, q: event.target.value, page: 1 }))} />
        </label>
        <label className={styles.filter}>
          <span className="sr-only">Статус проверки</span>
          <select value={query.status} onChange={(event) => setQuery((current) => ({ ...current, status: event.target.value as HistoryStatus, page: 1 }))}>
            {HISTORY_STATUSES.map((status) => <option key={status.value} value={status.value}>{status.label}</option>)}
          </select>
        </label>
        <button type="button" className={styles.refreshButton} disabled={pending} onClick={() => setRevision((current) => current + 1)} aria-label="Обновить историю">
          <svg aria-hidden="true" viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"><path d="M16 7a6.5 6.5 0 1 0 .3 5M16 3v4h-4"/></svg>
          <span>Обновить</span>
        </button>
      </div>

      <div className={styles.summary}>
        <p role="status" aria-live="polite">{data ? `${filtered ? "Найдено" : "Всего проверок"}: ${data.total}` : pending ? "Загружаем историю…" : "История недоступна"}</p>
        <span className={styles.historyLive}><i aria-hidden="true" />Обновляется автоматически</span>
      </div>

      {error && <div className={styles.historyError} role="alert"><p>{error}</p><button type="button" className={styles.secondaryButton} onClick={() => setRevision((current) => current + 1)} disabled={pending}>Повторить</button></div>}

      <div className={styles.results} aria-busy={pending}>
        {!data && pending ? (
          <div className={styles.historyLoading} role="status"><span className={styles.loadingDot} aria-hidden="true" />Загружаем проверки…</div>
        ) : data && data.items.length === 0 ? (
          <div className={styles.empty}>
            <svg aria-hidden="true" viewBox="0 0 48 48" fill="none" stroke="currentColor" strokeWidth="1.5"><path d="M29 7H13a3 3 0 0 0-3 3v28a3 3 0 0 0 3 3h22a3 3 0 0 0 3-3V16L29 7Z"/><path d="M28 7v10h10M18 26h12M18 32h8"/></svg>
            <h2>{filtered ? "Проверки не найдены" : "Проверок пока нет"}</h2>
            <p>{filtered ? "Попробуйте другое имя файла или измените статус." : "Загрузите первую запись — она появится здесь сразу после отправки."}</p>
            {filtered ? <button type="button" className={styles.secondaryButton} onClick={reset}>Сбросить фильтры</button> : <Link href="/#upload" className={styles.primaryButton}>Загрузить запись</Link>}
          </div>
        ) : data && data.items.length > 0 ? (
          <div className={styles.historyFeed} data-view={view}>
            {groupByDay(data.items).map(([key, group]) => (
              <section key={key} className={styles.dayGroup} aria-labelledby={`history-day-${key}`}>
                <h2 id={`history-day-${key}`}>{group.label}</h2>
                <ul className={styles.videoCollection} aria-label={`Видео за ${group.label.toLowerCase()}`}>{group.items.map((item) => <HistoryVideo key={item.id} item={item} />)}</ul>
              </section>
            ))}
          </div>
        ) : null}
      </div>

      {pagination && pagination.total > 0 && <footer className={styles.pagination}>
        <p>{(pagination.page - 1) * pagination.pageSize + 1}–{Math.min(pagination.page * pagination.pageSize, pagination.total)} из {pagination.total}</p>
        <Pagination9 page={pagination.page} pageCount={pagination.pageCount} pending={pending} onPageChange={(page) => setQuery((current) => ({ ...current, page }))} />
      </footer>}
    </section>
  );
}
