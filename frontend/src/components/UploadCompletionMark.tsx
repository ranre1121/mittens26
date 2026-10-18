"use client";

import { useEffect, useRef } from "react";

export function UploadCompletionMark({ onFinished }: { onFinished: () => void }) {
  const onFinishedRef = useRef(onFinished);

  useEffect(() => {
    onFinishedRef.current = onFinished;
  }, [onFinished]);

  useEffect(() => {
    const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    // Allow the 800 ms line drawing to finish, then hold the completed mark.
    const timer = setTimeout(() => onFinishedRef.current(), reducedMotion ? 450 : 1200);
    return () => clearTimeout(timer);
  }, []);

  return (
    <svg className="upload-completion-mark" viewBox="0 0 72 72" fill="none" aria-hidden="true">
      <circle className="upload-completion-ring" cx="36" cy="36" r="28" pathLength="100" transform="rotate(-90 36 36)" />
      <path className="upload-completion-check" d="m23 36 9 9 18-19" pathLength="100" />
    </svg>
  );
}
