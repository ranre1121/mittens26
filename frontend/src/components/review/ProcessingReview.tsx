import type { RefObject } from "react";
import type { JobView } from "@/lib/types";
import { formatBytes } from "@/lib/format";
import { CheckIcon, RecordIcon } from "@/components/review/ReviewShell";
import styles from "@/components/review/processing.module.css";

const dateFormat = new Intl.DateTimeFormat("ru-RU", {
  day: "2-digit", month: "long", year: "numeric", hour: "2-digit", minute: "2-digit", timeZone: "Asia/Almaty",
});

const stageLabels: Record<string, string> = { claimed: "Подготовка записи", probing: "Проверка видео", detecting: "Поиск событий", evidence: "Подготовка кадров", saving: "Сохранение результата" };

export function ProcessingReview({ job, videoRef }: {
  job: Extract<JobView, { status: "processing" }>;
  videoRef: RefObject<HTMLVideoElement | null>;
}) {
  const queued = job.state === "queued";
  const progress = Number.isFinite(job.progress) ? Math.round(Math.max(0, Math.min(100, job.progress))) : 0;

  return (
    <div className={styles.board}>
      <section className={styles.recording} aria-labelledby="source-video-title">
        <header className={styles.recordingHeader}>
          <h2 id="source-video-title"><RecordIcon /> Исходная запись</h2>
          <span><CheckIcon /> Файл принят</span>
        </header>
        <div className={styles.player}>
          <video ref={videoRef} src={`/api/jobs/${job.job_id}/video`} controls playsInline preload="metadata" aria-label={`Исходная запись: ${job.filename}`} />
        </div>
        <p className={styles.playerNote}>Видео доступно для просмотра во время проверки.</p>
      </section>

      <section className={styles.analysis} aria-labelledby="analysis-title">
        <div className={styles.status} role="status"><span aria-hidden="true" />{queued ? "Видео принято" : "Проверка выполняется"}</div>
        <h2 id="analysis-title" aria-live="polite">{queued ? "В очереди на анализ" : "Идёт анализ видео"}</h2>
        <p className={styles.description}>{queued ? "Всё готово. Проверка начнётся автоматически, когда подойдёт очередь." : "Проверяем кассовые операции и отмечаем фрагменты, требующие внимания."}</p>

        {!queued && <div className={styles.progress}>
          <div className={styles.progressLabel}><span>Обработано</span><strong>{progress}%</strong></div>
          <div className={styles.progressTrack} role="progressbar" aria-label="Прогресс анализа" aria-valuemin={0} aria-valuemax={100} aria-valuenow={progress}>
            <span style={{ transform: `scaleX(${progress / 100})` }} />
          </div>
        </div>}

        <ol className={styles.steps} aria-label="Этапы проверки">
          <li data-state="done">
            <span className={styles.stepMark}><CheckIcon /></span>
            <div><h3>Запись загружена</h3><p>Файл принят сервером</p></div>
          </li>
          <li data-state="active" aria-current="step">
            <span className={styles.stepMark} aria-hidden="true">
              <span className={styles.activitySquares}><span /><span /><span /></span>
            </span>
            <div><h3>{queued ? "Ожидание анализа" : "Анализ записи"}</h3><p>{queued ? `В очереди на обработку${job.queue_position != null ? ` · перед вами: ${job.queue_position}` : ""}` : (stageLabels[job.stage ?? ""] ?? "Проверка кассовых операций")}</p></div>
          </li>
          <li data-state="pending">
            <span className={styles.stepMark}>03</span>
            <div><h3>Результат проверки</h3><p>Отчёт, таймкоды и кадры</p></div>
          </li>
        </ol>

        <div className={styles.autoUpdate}>
          <svg aria-hidden="true" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"><path d="M20 7v5h-5M4 17v-5h5" /><path d="M6.1 7a7 7 0 0 1 11.5-1L20 9M4 15l2.4 3A7 7 0 0 0 17.9 17" /></svg>
          <p>Результат появится автоматически.<span>Можно перейти в историю — анализ продолжится.</span></p>
        </div>
      </section>

      <dl className={styles.metadata} aria-label="Сведения о проверке">
        <div><dt>Номер проверки</dt><dd>{job.job_id}</dd></div>
        <div><dt>Загружено <span>· Алматы</span></dt><dd><time dateTime={new Date(job.created_at * 1000).toISOString()}>{dateFormat.format(job.created_at * 1000)}</time></dd></div>
        <div><dt>Размер записи</dt><dd>{formatBytes(job.size_bytes)}</dd></div>
      </dl>
    </div>
  );
}
