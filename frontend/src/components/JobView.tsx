"use client";

import Link from "next/link";
import { useEffect, useRef, useState } from "react";
import type { EvidenceFrame, JobView as Job } from "@/lib/types";
import { formatBytes, formatTimecode } from "@/lib/format";
import { JobLoading, RecordIcon, ReviewHeading } from "@/components/review/ReviewShell";
import { AnalysisDashboard } from "@/components/review/AnalysisDashboard";
import { ProcessingReview } from "@/components/review/ProcessingReview";
import Button10 from "@/components/watermelon/button-10";
import styles from "@/components/review/review.module.css";
import { apiFetch } from "@/lib/session";

const POLL_MS = 2000;
const dateFormat = new Intl.DateTimeFormat("ru-RU", { day: "2-digit", month: "long", year: "numeric", hour: "2-digit", minute: "2-digit", timeZone: "Asia/Almaty" });

type Load =
  | { kind: "loading" }
  | { kind: "missing" }
  | { kind: "error"; message: string }
  | { kind: "ready"; job: Job };

function useJob(id: string): Load {
  const [load, setLoad] = useState<Load>({ kind: "loading" });

  useEffect(() => {
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout>;

    async function poll() {
      try {
        const res = await apiFetch(`/api/jobs/${id}`, { cache: "no-store" });
        if (cancelled) return;
        if (res.status === 404) return setLoad({ kind: "missing" });
        if (!res.ok) throw new Error(`Ошибка сервера (${res.status})`);
        const job = (await res.json()) as Job;
        if (cancelled) return;
        setLoad({ kind: "ready", job });
        if (job.status === "processing") timer = setTimeout(poll, POLL_MS);
      } catch (err) {
        if (cancelled) return;
        // Keep showing the last good state; just retry a little slower.
        setLoad((prev) => prev.kind === "ready" ? prev : { kind: "error", message: (err as Error).message });
        timer = setTimeout(poll, POLL_MS * 2);
      }
    }

    poll();
    return () => { cancelled = true; clearTimeout(timer); };
  }, [id]);

  return load;
}

export function JobView({ id }: { id: string }) {
  const load = useJob(id);
  const videoRef = useRef<HTMLVideoElement>(null);
  const [zoomed, setZoomed] = useState<EvidenceFrame | null>(null);

  function seek(sec: number) {
    const video = videoRef.current;
    if (!video) return;
    video.currentTime = sec;
    video.pause();
    video.scrollIntoView({ behavior: window.matchMedia("(prefers-reduced-motion: reduce)").matches ? "instant" : "smooth", block: "center" });
  }

  if (load.kind === "loading") return <JobLoading />;
  if (load.kind === "missing") return <Notice title="Проверка не найдена">Проверьте ссылку или найдите нужную запись в истории.</Notice>;
  if (load.kind === "error") return <Notice title="Не удалось получить статус">{load.message}. Повторяем запрос автоматически.</Notice>;

  const { job } = load;

  return (
    <>
      <ReviewHeading section="Проверка" title={job.filename} back description={<span className={styles.recordMeta}>Проверка <span>№ {job.job_id}</span><span aria-hidden="true">·</span>{formatBytes(job.size_bytes)}</span>} />
      {job.status === "done" ? <AnalysisDashboard job={job} videoRef={videoRef} onSeek={seek} onZoom={setZoomed} /> : job.status === "processing" ? <ProcessingReview job={job} videoRef={videoRef} /> : <>
      <FailureBanner job={job} />
      <div className={styles.reviewGrid}>
        <section className={styles.videoPanel} aria-labelledby="source-video-title">
          <header className={styles.panelHeading}>
            <h2 id="source-video-title"><RecordIcon /> Исходная запись</h2>
            <span>Видео</span>
          </header>
          <video ref={videoRef} src={`/api/jobs/${job.job_id}/video`} controls playsInline preload="metadata" className={styles.video} aria-label={`Исходная запись: ${job.filename}`} />
          <div className={styles.videoCaption}>
            <p>Исходный файл, отправленный на анализ.</p>
            <span>{formatBytes(job.size_bytes)}</span>
          </div>
        </section>

          <section className={styles.detailsPanel} aria-labelledby="record-details-title">
            <h2 id="record-details-title">Сведения о проверке</h2>
            <dl className={styles.details}>
              <div><dt>Номер</dt><dd>{job.job_id}</dd></div>
              <div><dt>Загружено</dt><dd><time dateTime={new Date(job.created_at * 1000).toISOString()}>{dateFormat.format(job.created_at * 1000)}</time></dd></div>
              <div><dt>Часовой пояс</dt><dd>Алматы</dd></div>
              <div><dt>Размер записи</dt><dd>{formatBytes(job.size_bytes)}</dd></div>
              {job.finished_at != null && <div><dt>Завершено</dt><dd><time dateTime={new Date(job.finished_at * 1000).toISOString()}>{dateFormat.format(job.finished_at * 1000)}</time></dd></div>}
            </dl>
            <div className={styles.detailsNote}>
              <span className={styles.noteMark} aria-hidden="true">i</span>
              <p>Исходная запись сохранена. Можно загрузить видео повторно или вернуться в историю.</p>
            </div>
            <Link href="/history" className={styles.textLink}>Все проверки <span aria-hidden="true">→</span></Link>
          </section>
      </div>
      </>}
      <div className={styles.reviewFooter}><Link href="/history" className={styles.secondaryButton}>Вернуться в историю</Link><Button10 href="/#upload">Проверить другое видео <svg aria-hidden="true" viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.5"><path d="M4 10h12m-5-5 5 5-5 5" /></svg></Button10></div>
      {zoomed && <EvidenceDialog frame={zoomed} onClose={() => setZoomed(null)} onSeek={seek} />}
    </>
  );
}

function FailureBanner({ job }: { job: Extract<Job, { status: "failed" }> }) {
  return (
    <section className={styles.verdictPanel} data-tone="error" aria-labelledby="verdict-title">
      <div className={styles.verdictSymbol} aria-hidden="true">!</div>
      <div className={styles.verdictCopy}>
        <p className={styles.eyebrow}>Обработка прервана</p>
        <h2 id="verdict-title">Не удалось обработать видео</h2>
        <p>{job.error ?? "Обработка завершилась ошибкой. Попробуйте отправить запись ещё раз."}</p>
      </div>
      <Link href="/#upload" className={styles.secondaryButton}>Загрузить заново</Link>
    </section>
  );
}

function EvidenceDialog({ frame, onClose, onSeek }: { frame: EvidenceFrame; onClose: () => void; onSeek: (sec: number) => void }) {
  const dialogRef = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    const dialog = dialogRef.current;
    if (!dialog) return;
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    dialog.showModal();
    return () => { dialog.close(); document.body.style.overflow = previousOverflow; };
  }, []);

  return (
    <dialog ref={dialogRef} className={styles.evidenceDialog} aria-labelledby="evidence-title" onCancel={(event) => { event.preventDefault(); onClose(); }} onClick={(event) => { if (event.target === event.currentTarget) onClose(); }}>
      <header><h2 id="evidence-title">Кадр на {formatTimecode(frame.timestamp_sec)}</h2><button type="button" className={styles.dialogButton} onClick={onClose} aria-label="Закрыть кадр">×</button></header>
      {/* eslint-disable-next-line @next/next/no-img-element -- evidence JPEG served by FastAPI at its natural proportions */}
      <img src={frame.url} alt={`Кадр на ${formatTimecode(frame.timestamp_sec)}`} />
      <footer className={styles.evidenceActions}><p>Кадр из исходной записи</p><button type="button" className={styles.dialogButton} onClick={() => { dialogRef.current?.close(); onClose(); onSeek(frame.timestamp_sec); }}>Перейти к кадру <span aria-hidden="true">↗</span></button></footer>
    </dialog>
  );
}

function Notice({ title, children }: { title: string; children: React.ReactNode }) {
  return <section className={styles.notice}><span className={styles.noticeIcon} aria-hidden="true">!</span><h1>{title}</h1><p>{children}</p><div><Link href="/history" className={styles.primaryButton}>История проверок</Link><Link href="/#upload" className={styles.secondaryButton}>Новая проверка</Link></div></section>;
}
