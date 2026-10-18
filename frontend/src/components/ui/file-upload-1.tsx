"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import Link from "next/link";
import metallic from "@/components/watermelon/button-10.module.css";
import { formatBytes } from "@/lib/format";
import { ParticleUploadWave } from "@/components/ParticleUploadWave";
import { UploadArtwork } from "@/components/UploadArtwork";
import { UploadCompletionMark } from "@/components/UploadCompletionMark";
import { UploadPageTransition } from "@/components/UploadPageTransition";
import { jobHref } from "@/lib/job-links";
import { CSRF_HEADER, notifyAuthRequired } from "@/lib/session";

// Set at build time: 95 on Cloudflare Workers (100 MB request limit), 500 self-hosted.
const MAX_MB = Number(process.env.NEXT_PUBLIC_MAX_UPLOAD_MB ?? 500);
const MAX_BYTES = MAX_MB * 1024 * 1024;
const ACCEPT = ".mov,.mp4,.m4v,video/quicktime,video/mp4";
// File drags are tracked just outside the panel so the outline can meet them.
const ATTRACTION_DISTANCE = 84;

type Edge = "top" | "right" | "bottom" | "left";
type Outline = { edge: Edge; anchor: number; pull: number };
type SpringOutline = Outline & { velocity: number };

function outlinePath(width: number, height: number, outline: Outline) {
  const radius = Math.min(16, width / 4, height / 4);
  const horizontalSpan = Math.min(82, Math.max(24, (width - radius * 2) / 4));
  const verticalSpan = Math.min(64, Math.max(24, (height - radius * 2) / 4));
  const horizontalAnchor = Math.max(
    radius + horizontalSpan,
    Math.min(width - radius - horizontalSpan, outline.anchor),
  );
  const verticalAnchor = Math.max(
    radius + verticalSpan,
    Math.min(height - radius - verticalSpan, outline.anchor),
  );
  const p = outline.pull;
  let path = `M ${radius} 0`;

  if (outline.edge === "top" && p > 0.1) {
    const a = horizontalAnchor;
    const s = horizontalSpan;
    path += ` L ${a - s} 0 C ${a - s * 0.5} 0 ${a - s * 0.5} ${-p} ${a} ${-p}`;
    path += ` C ${a + s * 0.5} ${-p} ${a + s * 0.5} 0 ${a + s} 0`;
  }
  path += ` L ${width - radius} 0 Q ${width} 0 ${width} ${radius}`;

  if (outline.edge === "right" && p > 0.1) {
    const a = verticalAnchor;
    const s = verticalSpan;
    path += ` L ${width} ${a - s} C ${width} ${a - s * 0.5} ${width + p} ${a - s * 0.5} ${width + p} ${a}`;
    path += ` C ${width + p} ${a + s * 0.5} ${width} ${a + s * 0.5} ${width} ${a + s}`;
  }
  path += ` L ${width} ${height - radius} Q ${width} ${height} ${width - radius} ${height}`;

  if (outline.edge === "bottom" && p > 0.1) {
    const a = horizontalAnchor;
    const s = horizontalSpan;
    path += ` L ${a + s} ${height} C ${a + s * 0.5} ${height} ${a + s * 0.5} ${height + p} ${a} ${height + p}`;
    path += ` C ${a - s * 0.5} ${height + p} ${a - s * 0.5} ${height} ${a - s} ${height}`;
  }
  path += ` L ${radius} ${height} Q 0 ${height} 0 ${height - radius}`;

  if (outline.edge === "left" && p > 0.1) {
    const a = verticalAnchor;
    const s = verticalSpan;
    path += ` L 0 ${a + s} C 0 ${a + s * 0.5} ${-p} ${a + s * 0.5} ${-p} ${a}`;
    path += ` C ${-p} ${a - s * 0.5} 0 ${a - s * 0.5} 0 ${a - s}`;
  }
  return `${path} L 0 ${radius} Q 0 0 ${radius} 0 Z`;
}

function closestEdge(x: number, y: number, width: number, height: number): Edge {
  const distances: [Edge, number][] = [
    ["top", Math.abs(y)],
    ["right", Math.abs(width - x)],
    ["bottom", Math.abs(height - y)],
    ["left", Math.abs(x)],
  ];
  return distances.reduce((closest, edge) =>
    edge[1] < closest[1] ? edge : closest,
  )[0];
}

type UploadEntry = {
  id: number;
  file: File;
  status: "queued" | "uploading" | "done" | "error";
  loaded: number;
  jobId?: string;
  error?: string;
};

/** XHR rather than fetch, because fetch can't report upload progress. */
function upload(file: File, onProgress: (loaded: number) => void) {
  return new Promise<string>((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open("POST", "/api/jobs");
    xhr.setRequestHeader(...CSRF_HEADER);
    xhr.upload.onprogress = (e) => onProgress(e.loaded);
    xhr.onload = () => {
      let body: { job_id?: string; detail?: unknown } = {};
      try {
        body = JSON.parse(xhr.responseText);
      } catch {}
      if (xhr.status === 401) notifyAuthRequired();
      if (xhr.status < 300 && body.job_id) resolve(body.job_id);
      else reject(new Error(typeof body.detail === "string" ? body.detail : `Ошибка сервера (${xhr.status})`));
    };
    xhr.onerror = () => reject(new Error("Нет соединения с сервером"));
    const form = new FormData();
    form.append("file", file);
    xhr.send(form);
  });
}

export function FileUploader() {
  const inputRef = useRef<HTMLInputElement>(null);
  const artworkRef = useRef<HTMLSpanElement>(null);
  const previousCountRef = useRef(0);
  const panelRef = useRef<HTMLDivElement>(null);
  const outlineRef = useRef<SVGPathElement>(null);
  const svgRef = useRef<SVGSVGElement>(null);
  const nextIdRef = useRef(0);
  const sendingRef = useRef(false);
  const targetRef = useRef<Outline>({ edge: "top", anchor: 0, pull: 0 });
  const springRef = useRef<SpringOutline>({
    edge: "top",
    anchor: 0,
    pull: 0,
    velocity: 0,
  });
  const [entries, setEntries] = useState<UploadEntry[]>([]);
  const [sending, setSending] = useState(false);
  const [activeId, setActiveId] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [dragging, setDragging] = useState(false);
  const [additionWave, setAdditionWave] = useState<{ id: number; count: number; complete: boolean } | null>(null);
  const [batchIds, setBatchIds] = useState<number[]>([]);
  const [transitionHref, setTransitionHref] = useState<string | null>(null);

  const addFiles = useCallback((files: File[]) => {
    if (sendingRef.current) return;
    const valid: UploadEntry[] = [];
    let rejected = 0;
    for (const file of files) {
      if (!/\.(mov|mp4|m4v)$/i.test(file.name) || file.size === 0 || file.size > MAX_BYTES) {
        rejected++;
        continue;
      }
      valid.push({ id: nextIdRef.current++, file, status: "queued", loaded: 0 });
    }
    if (valid.length) {
      setEntries((current) => [...current, ...valid]);
      setAdditionWave({ id: valid[valid.length - 1].id, count: valid.length, complete: false });
    }
    setError(rejected ? `${rejected} файл(ов) не добавлено. Допускаются непустые .mov, .mp4 и .m4v до ${MAX_MB} МБ.` : null);
  }, []);

  useEffect(() => {
    if (entries.length > previousCountRef.current && !window.matchMedia("(prefers-reduced-motion: reduce)").matches) {
      artworkRef.current?.animate(
        [{ transform: "translateY(0) scale(1)" }, { transform: "translateY(-7px) scale(1.04)" }, { transform: "translateY(0) scale(1)" }],
        { duration: 280, easing: "cubic-bezier(.2,.7,.2,1)" },
      );
    }
    previousCountRef.current = entries.length;
  }, [entries.length]);

  const updateEntry = (id: number, changes: Partial<UploadEntry>) => {
    setEntries((current) => current.map((entry) =>
      entry.id === id ? { ...entry, ...changes } : entry,
    ));
  };

  const sendFiles = async () => {
    if (sendingRef.current) return;
    const pending = entries.filter((entry) => entry.status === "queued" || entry.status === "error");
    if (!pending.length) return;
    sendingRef.current = true;
    setSending(true);
    setAdditionWave(null);
    setError(null);
    setTransitionHref(null);
    setBatchIds(pending.map((entry) => entry.id));
    const completed = new Map(entries.filter((entry) => entry.jobId).map((entry) => [entry.id, entry.jobId!]));
    let failed = false;

    for (const entry of pending) {
      setActiveId(entry.id);
      updateEntry(entry.id, { status: "uploading", loaded: 0, error: undefined });
      try {
        const jobId = await upload(entry.file, (loaded) => updateEntry(entry.id, { loaded }));
        updateEntry(entry.id, { status: "done", loaded: entry.file.size, jobId });
        completed.set(entry.id, jobId);
      } catch (cause) {
        failed = true;
        updateEntry(entry.id, { status: "error", error: (cause as Error).message });
      }
    }

    setActiveId(null);
    if (!failed) {
      const jobs = entries.map((entry) => completed.get(entry.id)).filter((id): id is string => Boolean(id));
      // The final portion of the wave requires every server acknowledgement.
      setTransitionHref(jobHref(jobs[0], jobs));
      return;
    }
    setSending(false);
    sendingRef.current = false;
  };

  const activeEntry = entries.find((entry) => entry.id === activeId);
  const busy = activeEntry?.status === "uploading";
  const showingAddition = !sending && additionWave !== null;
  const showingWave = showingAddition;
  const pct = activeEntry
    ? Math.min(100, Math.round((activeEntry.loaded / activeEntry.file.size) * 100))
    : 0;
  const pendingCount = entries.filter((entry) => entry.status === "queued" || entry.status === "error").length;
  const batch = entries.filter((entry) => batchIds.includes(entry.id));
  const batchSize = batch.reduce((sum, entry) => sum + entry.file.size, 0);
  const batchLoaded = batch.reduce((sum, entry) => sum + Math.min(entry.loaded, entry.file.size), 0);
  const pageProgress = transitionHref ? 100 : batchSize ? Math.min(97, batchLoaded / batchSize * 97) : 0;

  useEffect(() => {
    const panel = panelRef.current;
    const svg = svgRef.current;
    const outline = outlineRef.current;
    if (!panel || !svg || !outline) return;

    let frame = 0;
    const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

    const draw = () => {
      const { width, height } = panel.getBoundingClientRect();
      svg.setAttribute("viewBox", `0 0 ${width} ${height}`);
      outline.setAttribute("d", outlinePath(width, height, springRef.current));
    };

    const animate = () => {
      frame = 0;
      const current = springRef.current;
      const target = targetRef.current;
      current.anchor += (target.anchor - current.anchor) * 0.24;
      current.velocity = (current.velocity + (target.pull - current.pull) * 0.16) * 0.76;
      current.pull = Math.max(0, current.pull + current.velocity);
      draw();
      if (
        Math.abs(current.pull - target.pull) > 0.15 ||
        Math.abs(current.velocity) > 0.15 ||
        Math.abs(current.anchor - target.anchor) > 0.5
      ) {
        frame = requestAnimationFrame(animate);
      }
    };

    const attract = (target: Outline, active: boolean) => {
      const current = springRef.current;
      if (current.edge !== target.edge) {
        current.edge = target.edge;
        current.anchor = target.anchor;
        current.pull = 0;
        current.velocity = 0;
      }
      targetRef.current = target;
      setDragging(active);
      if (reducedMotion) {
        springRef.current = { ...target, velocity: 0 };
        draw();
      } else if (!frame) {
        frame = requestAnimationFrame(animate);
      }
    };

    const reset = () =>
      attract({ ...targetRef.current, pull: 0 }, false);

    const position = (event: DragEvent) => {
      const rect = panel.getBoundingClientRect();
      const x = event.clientX - rect.left;
      const y = event.clientY - rect.top;
      const dx = Math.max(-x, 0, x - rect.width);
      const dy = Math.max(-y, 0, y - rect.height);
      return { x, y, rect, distance: Math.hypot(dx, dy) };
    };

    const onDragOver = (event: DragEvent) => {
      if (sending || !Array.from(event.dataTransfer?.types ?? []).includes("Files")) {
        reset();
        return;
      }
      const { x, y, rect, distance } = position(event);
      if (distance > ATTRACTION_DISTANCE) {
        reset();
        return;
      }
      event.preventDefault();
      if (event.dataTransfer) event.dataTransfer.dropEffect = "copy";
      const edge = closestEdge(x, y, rect.width, rect.height);
      const anchor = edge === "top" || edge === "bottom" ? x : y;
      const pull = distance > 0
        ? Math.min(26, (ATTRACTION_DISTANCE - distance) * 0.7)
        : 0;
      attract({ edge, anchor, pull }, true);
    };

    const onDrop = (event: DragEvent) => {
      const { distance } = position(event);
      if (distance > ATTRACTION_DISTANCE || sending) return;
      event.preventDefault();
      reset();
      addFiles(Array.from(event.dataTransfer?.files ?? []));
    };

    const onDragLeave = (event: DragEvent) => {
      if (
        event.clientX <= 0 || event.clientY <= 0 ||
        event.clientX >= window.innerWidth || event.clientY >= window.innerHeight
      ) reset();
    };

    const observer = new ResizeObserver(draw);
    observer.observe(panel);
    draw();
    window.addEventListener("dragover", onDragOver);
    window.addEventListener("drop", onDrop);
    window.addEventListener("dragend", reset);
    window.addEventListener("dragleave", onDragLeave);
    return () => {
      cancelAnimationFrame(frame);
      observer.disconnect();
      window.removeEventListener("dragover", onDragOver);
      window.removeEventListener("drop", onDrop);
      window.removeEventListener("dragend", reset);
      window.removeEventListener("dragleave", onDragLeave);
    };
  }, [sending, addFiles]);

  const totalSize = entries.reduce((total, entry) => total + entry.file.size, 0);
  const failedCount = entries.filter((entry) => entry.status === "error").length;
  const completedCount = entries.filter((entry) => entry.status === "done").length;
  const clearAll = () => {
    if (sendingRef.current) return;
    setEntries([]);
    setError(null);
    setAdditionWave(null);
    if (inputRef.current) inputRef.current.value = "";
  };

  // Layout adapted from https://registry.watermelon.sh/r/file-upload-1.json.
  // Uploads use the application's real API; progress comes from XHR.
  return (
    <section className="upload-card" aria-labelledby="upload-title" data-ui>
      {sending && <UploadPageTransition progress={pageProgress} href={transitionHref} filename={activeEntry?.file.name} />}
      <header className="upload-card-header">
        <div>
          <h2 id="upload-title">Новая проверка</h2>
          <p>Добавьте записи с камеры над кассой.</p>
        </div>
        {entries.length > 0 && <span className="upload-count" aria-label={`Файлов в списке: ${entries.length}`}>{entries.length}</span>}
      </header>

      <div className="upload-card-content">
        <label htmlFor="video-files" className="sr-only">Видеозаписи для проверки</label>
        <input
          ref={inputRef}
          id="video-files"
          type="file"
          accept={ACCEPT}
          multiple
          disabled={sending}
          tabIndex={-1}
          className="sr-only"
          aria-describedby={error ? "upload-formats upload-error" : "upload-formats"}
          aria-invalid={Boolean(error)}
          onChange={(event) => {
            addFiles(Array.from(event.target.files ?? []));
            event.target.value = "";
          }}
        />
        <div ref={panelRef} className="upload-dropzone" data-dragging={dragging} data-busy={Boolean(busy)} data-animating={Boolean(showingWave)}>
          <svg ref={svgRef} aria-hidden="true" className="upload-elastic-outline" preserveAspectRatio="none">
            <path
              ref={outlineRef}
              fill={showingWave ? "none" : dragging ? "var(--upload-dropzone-active)" : "none"}
              stroke={dragging ? "var(--accent)" : "var(--dropzone-border)"}
              strokeWidth={dragging ? "2" : "1.5"}
              strokeDasharray={dragging ? undefined : "6 6"}
            />
          </svg>
          {showingWave && (
            <ParticleUploadWave
              key={`added-${additionWave?.id}`}
              progress={100}
              onComplete={additionWave ? () => {
                setAdditionWave((current) => current?.id === additionWave.id ? { ...current, complete: true } : current);
              } : undefined}
            />
          )}
          <button
            type="button"
            className="upload-dropzone-trigger"
            disabled={sending}
            onClick={() => inputRef.current?.click()}
            aria-label="Выберите файлы для проверки"
            aria-describedby="upload-formats"
          >
            <span className="upload-dropzone-idle" aria-hidden={Boolean(showingWave || busy)}>
              <span ref={artworkRef} className="upload-artwork-wrap"><UploadArtwork count={entries.length} /></span>
              <span className="upload-dropzone-title">{dragging ? "Отпустите, чтобы добавить" : "Перетащите видео сюда"}</span>
              <span className={`upload-browse ${metallic.button}`}>Выбрать файлы</span>
              <span id="upload-formats" className="upload-formats">MOV, MP4, M4V · до {MAX_MB} МБ на файл</span>
            </span>
          </button>
          {showingAddition && additionWave && (
            <div className="upload-progress-caption">
              {additionWave.complete && (
                <UploadCompletionMark key={`added-${additionWave.id}`} onFinished={() => {
                  setAdditionWave((current) => current?.id === additionWave.id ? null : current);
                }} />
              )}
              <p>{additionWave.count === 1 ? "Файл добавлен" : "Файлы добавлены"}</p>
              <span className="upload-active-filename">Готово к отправке</span>
            </div>
          )}
          {busy && activeEntry && (
            <div className="upload-progress-caption">
              <span className="upload-progress-value">{pct}%</span>
              <p>{pct === 100 ? "Сохраняем запись…" : "Загружаем видео…"}</p>
              <span className="upload-active-filename">{activeEntry.file.name}</span>
              <span className="sr-only" role="progressbar" aria-label={`Загрузка ${activeEntry.file.name}`} aria-valuemin={0} aria-valuemax={100} aria-valuenow={pct} />
            </div>
          )}
        </div>

        <div role="status" aria-live="polite" aria-atomic="true" className="sr-only">
          {sending ? `Отправляем файл: ${activeEntry?.file.name ?? ""}` : failedCount ? `Не удалось отправить файлов: ${failedCount}. Нажмите «Повторить отправку». Отправлено: ${completedCount}.` : completedCount ? `Отправлено на анализ: ${completedCount}.` : `Добавлено файлов: ${entries.length}.`}
        </div>
        {error && <p id="upload-error" className="upload-error" role="alert">{error}</p>}

        {entries.length > 0 ? (
          <div className="upload-file-section">
            <div className="upload-file-summary">
              <h3>Выбранные файлы</h3>
              <span>{formatBytes(totalSize)}</span>
            </div>
            <ul className="upload-file-list" aria-label="Выбранные видеозаписи">
              {entries.map((entry) => {
                const entryPct = Math.min(100, Math.round((entry.loaded / entry.file.size) * 100));
                return (
                  <li key={entry.id} className="upload-file-row" data-status={entry.status}>
                    <span className="upload-file-icon" aria-hidden="true">
                      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"><path d="M14 3H6a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V9z"/><path d="M14 3v6h6M10 12l5 3-5 3z"/></svg>
                    </span>
                    <div className="upload-file-info">
                      <p className="upload-file-name" title={entry.file.name}>{entry.file.name}</p>
                      <p className="upload-file-size">{formatBytes(entry.file.size)}</p>
                      <p className="upload-file-status">
                        {entry.status === "queued" && "Готов к отправке"}
                        {entry.status === "uploading" && `Загрузка · ${entryPct}%`}
                        {entry.status === "done" && "Отправлен на анализ"}
                        {entry.status === "error" && `Не удалось отправить: ${entry.error}`}
                      </p>
                      {entry.jobId && !sending && <Link href={jobHref(entry.jobId)} className="upload-result-link">Открыть проверку <span aria-hidden="true">↗</span></Link>}
                    </div>
                    {entry.status === "done" && <svg aria-label="Отправлен" role="img" className="upload-status-icon" viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.6"><circle cx="10" cy="10" r="8"/><path d="m6 10 3 3 5-6"/></svg>}
                    <button
                      type="button"
                      disabled={sending}
                      onClick={() => {
                        setEntries((current) => current.filter((item) => item.id !== entry.id));
                        setAdditionWave(null);
                      }}
                      aria-label={`Убрать ${entry.file.name} из списка`}
                      className={`upload-remove ${metallic.surface}`}
                    ><svg aria-hidden="true" viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round"><path d="m6 6 8 8M14 6l-8 8"/></svg></button>
                  </li>
                );
              })}
            </ul>
          </div>
        ) : <p className="upload-empty-note">Можно добавить несколько записей за один раз.</p>}
      </div>

      <footer className="upload-card-footer">
        <p>{sending ? "Отправка на анализ" : completedCount === entries.length && completedCount > 0 ? "Записи отправлены" : "Отправка после подтверждения"}</p>
        <div className="upload-actions">
          <button type="button" className={`button-secondary ${metallic.surface}`} disabled={sending || entries.length === 0} onClick={clearAll}>Очистить</button>
          <button type="button" className={`button-primary ${metallic.surface}`} disabled={sending || pendingCount === 0} onClick={() => void sendFiles()}>
            {sending ? "Отправляем…" : failedCount > 0 ? "Повторить отправку" : "Отправить"}
            {!sending && <svg aria-hidden="true" className="button-arrow" viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round"><path d="M4 10h12m-5-5 5 5-5 5"/></svg>}
          </button>
        </div>
      </footer>
    </section>
  );
}
