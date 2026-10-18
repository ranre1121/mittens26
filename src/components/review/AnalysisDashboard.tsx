"use client";

import type { RefObject } from "react";
import type { EvidenceFrame, JobView, Rule, Violation } from "@/lib/result";
import { formatTimecode } from "@/lib/format";
import { CheckIcon, RecordIcon } from "@/components/review/ReviewShell";
import styles from "./result.module.css";

type CompletedJob = Extract<JobView, { status: "done" }>;
const palette = ["var(--chart-1)", "var(--chart-2)", "var(--chart-3)", "var(--chart-4)", "var(--chart-5)", "var(--chart-6)"];
const finishedFormat = new Intl.DateTimeFormat("ru-RU", { day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit", timeZone: "Asia/Almaty" });

function safeSourceUrl(value?: string) {
  if (!value) return null;
  try { const url = new URL(value); return ["http:", "https:"].includes(url.protocol) ? url.href : null; } catch { return null; }
}

function elapsedLabel(start: number, end: number | null) {
  if (end == null || end < start) return "—";
  const seconds = Math.floor((end - start) / 1000);
  return seconds < 60 ? `${seconds} сек` : `${Math.floor(seconds / 60)} мин ${seconds % 60} сек`;
}

export function AnalysisDashboard({ job, videoRef, onSeek, onZoom }: {
  job: CompletedJob;
  videoRef: RefObject<HTMLVideoElement | null>;
  onSeek: (sec: number) => void;
  onZoom: (frame: EvidenceFrame) => void;
}) {
  const events = job.violations.map((violation, index) => ({ violation, index: index + 1 })).sort((a, b) => a.violation.timecode_sec - b.violation.timecode_sec);
  const types = new Map<string, number>();
  for (const event of events) types.set(event.violation.type, (types.get(event.violation.type) ?? 0) + 1);
  const frames = events.reduce((sum, event) => sum + event.violation.evidence_frames.length, 0);
  const missingSources = events.filter(({ violation }) => !violation.rule?.document?.trim() || !safeSourceUrl(violation.rule.url)).length;
  const segments = job.analysis?.unassessed_segments ?? [];

  return (
    <div className={styles.dashboard}>
      {job.analysis?.is_demo && <div className={styles.demoNotice} role="note"><strong>Демонстрационный результат</strong><p>Создан тестовым обработчиком. Он не подтверждает работу модели и не предназначен для оценки действий кассира.</p></div>}
      <div className={styles.board}>
        <section className={styles.summary} aria-labelledby="report-verdict">
          <div className={styles.summaryTop}><span className={styles.completion}><CheckIcon />Анализ завершён</span>{job.finished_at != null && <time dateTime={new Date(job.finished_at).toISOString()}>{finishedFormat.format(job.finished_at)} · Алматы</time>}</div>
          <h2 id="report-verdict">{events.length ? "Обнаружены признаки возможных нарушений" : "Нарушение не обнаружено"}</h2>
          <p>{events.length ? "Проверьте отмеченные фрагменты, наблюдаемые действия и основания каждого вывода." : "В пределах выполненной проверки визуальные признаки нарушений не найдены. Учитывайте ограничения записи."}</p>
          <dl className={styles.metrics}>
            <div><dt>События</dt><dd>{String(events.length).padStart(2, "0")}</dd></div>
            <div><dt>Типы событий</dt><dd>{String(types.size).padStart(2, "0")}</dd></div>
            <div><dt>Кадры-доказательства</dt><dd>{String(frames).padStart(2, "0")}</dd></div>
            <div><dt>До результата</dt><dd className={styles.elapsed}>{elapsedLabel(job.created_at, job.finished_at)}</dd><span>Включая очередь</span></div>
          </dl>
          {missingSources > 0 && <p className={styles.missingSourceNotice}>Событий без ссылки на нормативный источник: {missingSources}. Их нормативное основание требует ручной проверки.</p>}
          {segments.length > 0 && <p className={styles.missingSourceNotice}>Есть фрагменты, которые не удалось оценить. Они перечислены в ограничениях анализа.</p>}
        </section>

        <section className={styles.playerPanel} aria-labelledby="source-video-title">
          <header className={styles.panelHeading}><h2 id="source-video-title"><RecordIcon /> Исходная запись</h2><span>Видео</span></header>
          <video ref={videoRef} src={`/api/jobs/${job.job_id}/video`} controls playsInline preload="metadata" className={styles.video} aria-label={`Исходная запись: ${job.filename}`} />
          <div className={styles.playerBody}>
            <h3>Навигация по событиям</h3>
            {events.length > 0 ? <ol className={styles.eventNavigation}>{events.map(({ violation, index }) => <li key={index}><button type="button" className={styles.eventButton} onClick={() => onSeek(violation.timecode_sec)} aria-label={`Событие ${index}: перейти к ${formatTimecode(violation.timecode_sec)}`}><span className={styles.eventNumber}>{String(index).padStart(2, "0")}</span><span>{violation.type}</span><time>{formatTimecode(violation.timecode_sec)}</time></button></li>)}</ol> : <p className={styles.emptyNavigation}>Событий для перехода нет. Запись доступна для самостоятельного просмотра.</p>}
            <p className={styles.playerHint}>Таймкод открывает нужный момент записи. Кадр в отчёте можно увеличить.</p>
          </div>
        </section>

        <section className={styles.distribution} aria-labelledby="event-types-title">
          <header className={styles.panelHeading}><h2 id="event-types-title">Типы отмеченных событий</h2><span>{types.size}</span></header>
          <EventDistribution types={[...types.entries()]} total={events.length} />
        </section>

        <section className={styles.scope} aria-labelledby="analysis-scope-title">
          <header className={styles.panelHeading}><h2 id="analysis-scope-title">Область проверки</h2></header>
          <div className={styles.scopeBody}>
            <h3>Проверенные типы событий</h3>
            {job.analysis?.checked_types?.length ? <ul className={styles.checkedTypes}>{job.analysis.checked_types.map((type, index) => <li key={index}>{type}</li>)}</ul> : <p className={styles.unavailable}>Перечень проверенных типов событий не указан.</p>}
            <h3>Ограничения анализа</h3>
            {job.analysis?.limitations?.length ? <ul className={styles.limitations}>{job.analysis.limitations.map((limitation, index) => <li key={index}>{limitation}</li>)}</ul> : <p className={styles.unavailable}>Индивидуальные ограничения этой записи не указаны.</p>}
            {segments.length > 0 && <div className={styles.unassessed}><h3>Требуют ручного просмотра</h3>{segments.map((segment, index) => <div key={index}><button type="button" className={styles.segmentButton} onClick={() => onSeek(segment.start_sec)}>{formatTimecode(segment.start_sec)} — {formatTimecode(segment.end_sec)} <span aria-hidden="true">↗</span></button><p>{segment.reason}</p></div>)}</div>}
          </div>
        </section>
      </div>

      <section className={styles.evidenceSection} aria-labelledby="evidence-section-title">
        <header className={styles.evidenceHeading}><div><h2 id="evidence-section-title">События и доказательства</h2><p>Наблюдаемое действие → обоснование → нормативный источник</p></div><span>{String(events.length).padStart(2, "0")}</span></header>
        {events.length > 0 ? <><div className={styles.columnHeadings} aria-hidden="true"><span>Событие / таймкод</span><span>Наблюдение и обоснование</span><span>Кадры-доказательства</span><span>Правило и источник</span></div><ol className={styles.evidenceList}>{events.map(({ violation, index }) => <EventEvidence key={index} index={index} violation={violation} onSeek={onSeek} onZoom={onZoom} />)}</ol></> : <div className={styles.noEvents}><span><CheckIcon /></span><h3>События не отмечены</h3><p>Отсутствие обнаруженных событий не подтверждает корректность действий за пределами видимости камеры или перечня проверяемых типов.</p></div>}
      </section>
      <aside className={styles.reportNote}><span aria-hidden="true">i</span><p>Оценка относится к визуально наблюдаемым действиям. Корректность проводок и системных сверок по видео не подтверждается. Результат помогает специалисту проверить запись и не является окончательным юридическим или дисциплинарным заключением.</p></aside>
    </div>
  );
}

function EventDistribution({ types, total }: { types: [string, number][]; total: number }) {
  const slices = types.map(([type, count], index) => ({
    type, count, start: types.slice(0, index).reduce((sum, [, value]) => sum + value, 0) / total * 100,
    length: count / total * 100, color: palette[index % palette.length],
  }));
  return <div className={styles.distributionBody}><div className={styles.donut} aria-hidden="true"><svg viewBox="0 0 112 112"><circle cx="56" cy="56" r="44" fill="none" stroke="var(--border)" strokeWidth="10" />{slices.map((slice) => <circle key={slice.type} cx="56" cy="56" r="44" fill="none" stroke={slice.color} strokeWidth="10" pathLength="100" strokeDasharray={`${slice.length} 100`} strokeDashoffset={-slice.start} transform="rotate(-90 56 56)" />)}</svg><div><strong>{total}</strong><span>событий</span></div></div>{types.length ? <ul className={styles.legend}>{slices.map((slice) => <li key={slice.type}><i style={{ backgroundColor: slice.color }} aria-hidden="true" /><span>{slice.type}</span><strong>{slice.count}</strong></li>)}</ul> : <p className={styles.unavailable}>Типы нарушений не отмечены.</p>}</div>;
}

function EventEvidence({ index, violation, onSeek, onZoom }: { index: number; violation: Violation; onSeek: (sec: number) => void; onZoom: (frame: EvidenceFrame) => void }) {
  return <li className={styles.evidenceRow}>
    <div className={styles.eventIdentity}><span className={styles.eventIndex}>Событие {String(index).padStart(2, "0")}</span><h3>{violation.type}</h3>{violation.explanation && <p className={styles.eventDescription}>{violation.description}</p>}<button type="button" className={styles.timeButton} onClick={() => onSeek(violation.timecode_sec)} aria-label={`Перейти к моменту ${formatTimecode(violation.timecode_sec)} на видео`}><span aria-hidden="true">▷</span>{formatTimecode(violation.timecode_sec)}</button></div>
    <div className={styles.explanation}>{violation.explanation ? <><h4>Что видно на видео</h4><p>{violation.explanation.observation}</p><h4>Почему это может быть нарушением</h4><p>{violation.explanation.reasoning}</p></> : <><h4>Наблюдение и обоснование</h4><p>{violation.description}</p></>}</div>
    <div className={styles.frames}><h4>Кадры-доказательства <span>{violation.evidence_frames.length}</span></h4><div>{violation.evidence_frames.map((frame, frameIndex) => <button key={frameIndex} className={styles.frameButton} type="button" onClick={() => onZoom(frame)} aria-label={`Увеличить кадр ${frameIndex + 1} события ${index} на ${formatTimecode(frame.timestamp_sec)}`}>
      {/* eslint-disable-next-line @next/next/no-img-element -- base64 evidence; stable aspect ratio preserves the complete frame */}
      <img src={`data:image/jpeg;base64,${frame.image_base64}`} alt={`Событие ${index}, кадр на ${formatTimecode(frame.timestamp_sec)}`} loading="lazy" /><span>{formatTimecode(frame.timestamp_sec)}<span aria-hidden="true">↗</span></span></button>)}</div></div>
    <SourceReference rule={violation.rule} />
  </li>;
}

function SourceReference({ rule }: { rule?: Rule }) {
  const url = safeSourceUrl(rule?.url);
  return <div className={styles.source}><h4>Правило и источник</h4>{rule?.document?.trim() ? <><p className={styles.sourceDocument}>{rule.document}</p><p>{rule.clause?.trim() || "Пункт / раздел не указан"}</p>{url ? <a href={url} target="_blank" rel="noopener noreferrer">Открыть документ <span aria-hidden="true">↗</span></a> : <p className={styles.sourceMissing}>Ссылка на документ не указана.</p>}</> : <p className={styles.sourceMissing}>Источник не указан. Нормативное основание требует проверки.</p>}</div>;
}
