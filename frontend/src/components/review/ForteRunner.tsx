"use client";

import { useEffect, useId, useRef, useState, type KeyboardEvent, type PointerEvent } from "react";
import { advanceRun, createRun, jump, resizeRun, runnerX, RUN_GROUND, RUN_HEIGHT, startRun, type RunPhase } from "@/lib/forte-run";
import Button10 from "@/components/watermelon/button-10";
import styles from "./runner.module.css";

// The two f subpaths from the official wordmark in public/forte-wordmark.svg.
const FORTE_F_PATH = "M28.7665 0V8.09264H22.9844C16.3718 8.09264 10.7558 12.3952 8.88083 18.3129C5.13095 20.4319 2.04538 23.5587 0 27.3229V22.65C0 10.1599 10.31 0 22.9844 0H28.7665ZM22.9844 26.0869V17.9986C22.0666 17.9986 21.1662 18.0502 20.2791 18.1536C15.7163 18.6833 11.5556 20.5353 8.20777 23.3133C4.87746 26.0696 2.35569 29.7434 1.03143 33.934C0.36275 36.0573 0 38.3098 0 40.6484V45.3042H8.20777V40.6484C8.20777 32.6203 14.8421 26.0869 22.9844 26.0869Z";

const RECORD_KEY = "forte-run-best-v1";
type Controls = { play: () => void; jump: () => void; pause: () => void };
const digits = (value: number) => String(value).padStart(3, "0");

export function ForteRunner() {
  const id = useId().replace(/:/g, "");
  const fieldRef = useRef<HTMLDivElement>(null);
  const svgRef = useRef<SVGSVGElement>(null);
  const runnerRef = useRef<SVGGElement>(null);
  const safeRefs = useRef<(SVGUseElement | null)[]>([]);
  const controls = useRef<Controls | null>(null);
  const [view, setView] = useState<{ phase: RunPhase; score: number; best: number }>({ phase: "idle", score: 0, best: 0 });

  useEffect(() => {
    const field = fieldRef.current;
    const svg = svgRef.current;
    if (!field || !svg) return;
    const run = createRun(field.clientWidth || 960);
    let best = 0;
    try { const saved = Number(localStorage.getItem(RECORD_KEY)); if (Number.isSafeInteger(saved) && saved >= 0) best = saved; } catch { /* Storage may be unavailable in private mode. */ }
    let frame = 0;
    let previous = 0;

    const publish = () => setView({ phase: run.phase, score: run.score, best });
    const draw = () => {
      svg.setAttribute("viewBox", `0 0 ${run.width} ${RUN_HEIGHT}`);
      const x = runnerX(run.width);
      runnerRef.current?.setAttribute("transform", `translate(${x} ${RUN_GROUND - 48 - run.height})`);
      const safes = run.phase === "idle" ? [
        { x: run.width - 62, width: 38, height: 42 },
      ] : run.safes;
      safeRefs.current.forEach((element, index) => {
        if (!element) return;
        const safe = safes[index];
        element.style.display = safe ? "" : "none";
        if (safe) element.setAttribute("transform", `translate(${safe.x} ${RUN_GROUND - safe.height}) scale(${safe.width / 44} ${safe.height / 48})`);
      });
    };
    const tick = (time: number) => {
      const score = run.score;
      advanceRun(run, previous ? (time - previous) / 1000 : 0);
      previous = time;
      draw();
      if (run.score > best) {
        best = run.score;
        try { localStorage.setItem(RECORD_KEY, String(best)); } catch { /* Keep the record for this visit. */ }
      }
      if (score !== run.score || run.phase === "over") publish();
      if (run.phase === "running") frame = requestAnimationFrame(tick);
    };
    const play = () => {
      if (run.phase === "running") return;
      if (run.phase === "paused") run.phase = "running";
      else startRun(run);
      previous = 0;
      cancelAnimationFrame(frame);
      publish();
      draw();
      frame = requestAnimationFrame(tick);
    };
    const pause = () => {
      if (run.phase !== "running") return;
      run.phase = "paused";
      cancelAnimationFrame(frame);
      previous = 0;
      publish();
    };
    controls.current = { play, pause, jump: () => { play(); jump(run); } };
    const resize = new ResizeObserver(() => {
      const width = field.clientWidth;
      if (width <= 0 || Math.abs(width - run.width) < 1) return;
      pause();
      resizeRun(run, width);
      draw();
    });
    resize.observe(field);
    const visibility = () => { if (document.hidden) pause(); };
    const intersection = new IntersectionObserver(([entry]) => { if (!entry.isIntersecting) pause(); });
    intersection.observe(field);
    document.addEventListener("visibilitychange", visibility);
    window.addEventListener("blur", pause);
    frame = requestAnimationFrame(() => { publish(); draw(); });
    return () => {
      cancelAnimationFrame(frame);
      resize.disconnect();
      intersection.disconnect();
      document.removeEventListener("visibilitychange", visibility);
      window.removeEventListener("blur", pause);
      controls.current = null;
    };
  }, []);

  const play = () => { controls.current?.play(); fieldRef.current?.focus({ preventScroll: true }); };
  const onKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    if (event.target !== event.currentTarget) return;
    if (["Space", "ArrowUp"].includes(event.code)) {
      event.preventDefault();
      if (!event.repeat) controls.current?.jump();
    } else if (event.code === "Escape") controls.current?.pause();
  };
  const onPointerDown = (event: PointerEvent<HTMLDivElement>) => {
    if (!event.isPrimary || event.button !== 0 || (event.target instanceof Element && event.target.closest("button"))) return;
    event.preventDefault();
    fieldRef.current?.focus({ preventScroll: true });
    controls.current?.jump();
  };
  const title = view.phase === "idle" ? "Перепрыгивайте через сейфы" : view.phase === "paused" ? "Игра на паузе" : "Попытка завершена";

  return <section className={styles.game} aria-labelledby={`${id}-title`} onBlurCapture={(event) => { if (!event.currentTarget.contains(event.relatedTarget)) controls.current?.pause(); }}>
    <header className={styles.header}>
      <h2 id={`${id}-title`} className="sr-only">Forte Run</h2>
      <p id={`${id}-controls`} className={styles.hint}>
        <span className={styles.keyboard}><kbd>Пробел</kbd><kbd>↑</kbd><span>или</span></span> касание — прыжок
      </p>
      <div className={styles.toolbar}>
        <dl className={styles.scores}><div><dt>Счёт</dt><dd>{digits(view.score)}</dd></div><div><dt>Рекорд</dt><dd>{digits(view.best)}</dd></div></dl>
        <button type="button" className={styles.pause} disabled={view.phase === "idle" || view.phase === "over"} onClick={() => view.phase === "running" ? controls.current?.pause() : play()} aria-label={view.phase === "paused" ? "Продолжить игру" : "Пауза игры"}><svg viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth={view.phase === "paused" ? "1.5" : "2"} aria-hidden="true"><path d={view.phase === "paused" ? "m7 4 9 6-9 6V4Z" : "M7 5v10M13 5v10"} /></svg></button>
      </div>
    </header>
    <div ref={fieldRef} className={styles.field} role="group" tabIndex={0} aria-label="Игровое поле: фирменная f Forte прыгает через сейфы" aria-describedby={`${id}-controls`} onKeyDown={onKeyDown} onPointerDown={onPointerDown} data-phase={view.phase}>
      <svg ref={svgRef} className={styles.scene} viewBox={`0 0 960 ${RUN_HEIGHT}`} aria-hidden="true">
        <defs>
          <linearGradient id={`${id}-platinum`} x1="0" x2="1" y1="0" y2="1"><stop stopColor="#8b8296" /><stop offset=".32" stopColor="#eee8f4" /><stop offset=".5" stopColor="#a99eb6" /><stop offset=".54" stopColor="#766b84" /><stop offset="1" stopColor="#d1c5dd" /></linearGradient>
          <linearGradient id={`${id}-safe`} x1="0" x2="1" y1="0" y2="1"><stop stopColor="#62576f" /><stop offset=".4" stopColor="#403849" /><stop offset=".55" stopColor="#2c2633" /><stop offset="1" stopColor="#493c57" /></linearGradient>
          <pattern id={`${id}-grain`} width="4" height="4" patternUnits="userSpaceOnUse"><path d="M0 .5h4" stroke="#fff" strokeOpacity=".05" /></pattern>
          <g id={`${id}-vault`}>
            <rect x="1" y="1" width="42" height="46" rx="4" fill={`url(#${id}-safe)`} stroke="#897a99" />
            <rect x="1" y="1" width="42" height="46" rx="4" fill={`url(#${id}-grain)`} />
            <rect x="6" y="6" width="32" height="35" rx="2" fill="none" stroke="#a698b7" strokeOpacity=".5" />
            <path d="M8 13H5m3 20H5M9 45h5m16 0h5" stroke="#b1a5bd" strokeWidth="2" />
            <circle cx="23" cy="23" r="7" fill="#2b2530" stroke="#c0b1cf" strokeWidth="1.4" />
            <path d="M23 17v12m-6-6h12m-10-4 8 8m0-8-8 8" stroke="#b3a2c3" strokeWidth="1.1" />
            <circle cx="23" cy="23" r="2.2" fill="#d0c2dd" />
          </g>
        </defs>
        <g ref={runnerRef} transform={`translate(96 ${RUN_GROUND - 48})`} data-forte-runner="true">
          <path d={FORTE_F_PATH} transform="translate(3.76 0) scale(1.0595044)" fill={`url(#${id}-platinum)`} fillRule="evenodd" clipRule="evenodd" />
        </g>
        {Array.from({ length: 8 }, (_, index) => <use key={index} ref={(node) => { safeRefs.current[index] = node; }} href={`#${id}-vault`} style={{ display: "none" }} />)}
      </svg>
      {view.phase !== "running" && <div className={styles.overlay}>
        <p className={styles.overlayTitle}>{title}</p>
        {view.phase === "over" && <p className={styles.result}>Сейфов пройдено: <strong>{view.score}</strong></p>}
        <Button10 onClick={play} className={styles.play}><svg viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.5" aria-hidden="true"><path d="m7 4 9 6-9 6V4Z" /></svg>{view.phase === "idle" ? "Играть" : view.phase === "paused" ? "Продолжить" : "Ещё раз"}</Button10>
      </div>}
    </div>
    <span className="sr-only" role="status">{view.phase === "over" ? `Попытка завершена. Сейфов пройдено: ${view.score}.` : view.phase === "paused" ? "Игра на паузе." : view.phase === "running" ? "Игра началась. Перепрыгивайте сейфы." : "Игра готова к запуску."}</span>
  </section>;
}
