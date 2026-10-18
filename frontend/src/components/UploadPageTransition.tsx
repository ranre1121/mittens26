"use client";

import { createContext, useCallback, useContext, useEffect, useRef, useState, Suspense, type ReactNode } from "react";
import { createPortal } from "react-dom";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { ParticleUploadWave } from "@/components/ParticleUploadWave";
import { useJobCompletions } from "@/components/useJobCompletions";
import Announcement1 from "@/components/watermelon/announcement-1";

type Transition = { progress: number; href: string | null; filename?: string };
type Command = { type: "update"; value: Transition } | { type: "cancel" | "navigating" | "finish" };
type Dispatch = (command: Command) => void;
const TransitionContext = createContext<Dispatch | null>(null);

// The root layout keeps the same overlay alive while the route beneath it changes.
export function UploadTransitionProvider({ children }: { children: ReactNode }) {
  const [transition, setTransition] = useState<Transition | null>(null);
  const navigatingRef = useRef(false);
  const [currentJobId, setCurrentJobId] = useState<string | null>(null);
  const { completion, remaining, dismiss } = useJobCompletions(currentJobId);
  const dispatch = useCallback<Dispatch>((command) => {
    if (command.type === "update") {
      if (!navigatingRef.current) setTransition(command.value);
    } else if (command.type === "navigating") {
      navigatingRef.current = true;
    } else if (command.type === "finish" || !navigatingRef.current) {
      navigatingRef.current = false;
      setTransition(null);
    }
  }, []);

  return (
    <TransitionContext.Provider value={dispatch}>
      <Suspense fallback={null}><JobLocationTracker onChange={setCurrentJobId} /></Suspense>
      {!transition && <Announcement1 completion={completion} remaining={remaining} onDismiss={dismiss} />}
      {children}
      {transition && <TransitionOverlay {...transition} dispatch={dispatch} announcement={<Announcement1 completion={completion} remaining={remaining} onDismiss={dismiss} uploading />} />}
    </TransitionContext.Provider>
  );
}

export function UploadPageTransition({ progress, href, filename }: Transition) {
  const dispatch = useContext(TransitionContext);
  if (!dispatch) throw new Error("UploadPageTransition requires UploadTransitionProvider");

  useEffect(() => { dispatch({ type: "update", value: { progress, href, filename } }); }, [dispatch, progress, href, filename]);
  useEffect(() => () => dispatch({ type: "cancel" }), [dispatch]);
  return null;
}

function TransitionOverlay({ progress, href, filename, dispatch, announcement }: Transition & { dispatch: Dispatch; announcement: ReactNode }) {
  const router = useRouter();
  const pathname = usePathname();
  const overlayRef = useRef<HTMLDivElement>(null);
  const navigatingRef = useRef(false);
  const [covered, setCovered] = useState(false);
  const revealing = covered && Boolean(href && pathname.replace(/\/$/, "") === href.split("?")[0].replace(/\/$/, ""));
  const finish = useCallback(() => dispatch({ type: "finish" }), [dispatch]);
  const openJob = useCallback(() => {
    if (!href || navigatingRef.current) return;
    navigatingRef.current = true;
    dispatch({ type: "navigating" });
    router.push(href);
  }, [href, router, dispatch]);
  const finishFilling = useCallback(() => {
    setCovered(true);
    openJob();
  }, [openJob]);

  useEffect(() => {
    const previousFocus = document.activeElement;
    const overflow = document.body.style.overflow;
    const siblings = Array.from(document.body.children).filter(
      (element): element is HTMLElement => element instanceof HTMLElement && element !== overlayRef.current,
    ).map((element) => ({ element, inert: element.inert }));
    siblings.forEach(({ element }) => { element.inert = true; });
    document.body.style.overflow = "hidden";
    overlayRef.current?.focus({ preventScroll: true });
    return () => {
      document.body.style.overflow = overflow;
      siblings.forEach(({ element, inert }) => { element.inert = inert; });
      const focusTarget = previousFocus instanceof HTMLElement && previousFocus.isConnected
        ? previousFocus
        : document.getElementById("main-content");
      focusTarget?.focus({ preventScroll: true });
    };
  }, []);

  useEffect(() => {
    if (!href) return;
    router.prefetch(href);
    // Do not block navigation if animation frames are paused in a background tab.
    const timer = setTimeout(finishFilling, 2000);
    return () => clearTimeout(timer);
  }, [href, finishFilling, router]);

  useEffect(() => {
    if (!revealing) return;
    // Canvas completes the reveal; this only handles paused animation frames.
    const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    const timer = setTimeout(finish, reducedMotion ? 0 : 950);
    return () => clearTimeout(timer);
  }, [revealing, finish]);

  return createPortal(
    <div ref={overlayRef} className="upload-page-transition" data-revealing={revealing} role="dialog" aria-modal="true" aria-label="Отправка видеозаписей" tabIndex={-1} onKeyDown={(event) => { if (event.key === "Tab") event.preventDefault(); }}>
      <ParticleUploadWave progress={revealing ? 100 : progress} direction="up" mode={revealing ? "clear" : "fill"} duration={revealing ? 700 : 1200} completionDelay={0} onComplete={revealing ? finish : finishFilling} />
      {announcement}
      <p className="sr-only" role="status" aria-live="polite">
        {href ? "Записи загружены. Открываем проверку…" : `Отправляем файл: ${filename ?? ""}`}
      </p>
    </div>,
    document.body,
  );
}

// Only this tiny observer depends on query parameters; keep the page prerenderable.
function JobLocationTracker({ onChange }: { onChange: (id: string | null) => void }) {
  const pathname = usePathname();
  const params = useSearchParams();
  const id = pathname.replace(/\/$/, "") === "/jobs" ? params.get("id") : null;
  useEffect(() => { onChange(id); }, [id, onChange]);
  return null;
}
