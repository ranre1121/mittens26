"use client";

import { useEffect, useRef } from "react";

const PARTICLES = Array.from({ length: 420 }, (_, index) => ({
  x: (index * 0.61803398875) % 1,
  y: (index * 0.75487766625) % 1,
  radius: 1.8 + (index % 5) * 0.5,
  speed: 0.65 + (index % 7) * 0.12,
}));

type ParticleUploadWaveProps = {
  progress: number;
  onComplete?: () => void;
  direction?: "right" | "up";
  mode?: "fill" | "clear";
  duration?: number;
  completionDelay?: number;
};

export function ParticleUploadWave({ progress, onComplete, direction = "right", mode = "fill", duration = 1540, completionDelay = 240 }: ParticleUploadWaveProps) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const progressRef = useRef(progress);
  const onCompleteRef = useRef(onComplete);

  useEffect(() => {
    progressRef.current = progress;
  }, [progress]);

  useEffect(() => {
    onCompleteRef.current = onComplete;
  }, [onComplete]);

  useEffect(() => {
    const canvas = canvasRef.current;
    const context = canvas?.getContext("2d");
    if (!canvas || !context) return;

    const styles = window.getComputedStyle(canvas);
    const color = (token: string, fallback: string) => styles.getPropertyValue(token).trim() || fallback;
    const colors = {
      fill: color("--upload-wave-fill", "#342047"),
      particle: color("--upload-wave-particle", "#83649e"),
      highlight: color("--upload-wave-highlight", "#c4abd9"),
    };
    const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    let width = 0;
    let height = 0;
    let pixelRatio = 1;
    let displayed = 0;
    let velocity = 0;
    let previousTime = 0;
    let frame = 0;
    let completedAt: number | null = null;
    let completionNotified = false;
    let finalSweep: { start: number; from: number; duration: number } | null = null;

    const resize = () => {
      const rect = canvas.getBoundingClientRect();
      pixelRatio = Math.max(window.devicePixelRatio || 1, 1);
      width = rect.width;
      height = rect.height;
      canvas.width = Math.round(width * pixelRatio);
      canvas.height = Math.round(height * pixelRatio);
      if (width && height) {
        context.setTransform(canvas.width / width, 0, 0, canvas.height / height, 0, 0);
      }
    };

    const drawParticle = (x: number, y: number, radius: number, opacity: number, tint: string) => {
      if (x < -radius || x > width + radius) return;
      // Solid circles retain a crisp edge; the small highlight adds volume without blur.
      context.globalAlpha = opacity;
      context.fillStyle = tint;
      context.beginPath();
      context.arc(x, y, radius, 0, Math.PI * 2);
      context.fill();
      context.globalAlpha = opacity * 0.65;
      context.strokeStyle = colors.highlight;
      context.lineWidth = 0.5;
      context.stroke();
      context.globalAlpha = opacity * 0.8;
      context.fillStyle = colors.highlight;
      context.beginPath();
      context.arc(x - radius * 0.28, y - radius * 0.3, radius * 0.25, 0, Math.PI * 2);
      context.fill();
    };

    const draw = (time: number) => {
      frame = requestAnimationFrame(draw);
      const elapsed = Math.min(time - (previousTime || time), 64);
      previousTime = time;
      const target = Math.max(0, Math.min(progressRef.current / 100, 1));
      if (reducedMotion) {
        displayed = target;
      } else if (direction === "up" && target === 1) {
        // End on a fixed frame instead of lingering almost fully covered.
        finalSweep ??= { start: time, from: displayed, duration: Math.max(180, duration * (1 - displayed)) };
        const fraction = Math.min(1, (time - finalSweep.start) / finalSweep.duration);
        const eased = fraction * fraction * (3 - 2 * fraction);
        displayed = finalSweep.from + (1 - finalSweep.from) * eased;
      } else if (direction === "up") {
        // Critically damped motion eases into new progress without jumps or overshoot.
        const frequency = 10 / Math.max(1, duration);
        const offset = displayed - target;
        const momentum = velocity + frequency * offset;
        const decay = Math.exp(-frequency * elapsed);
        const maxSpeed = 1.4 / Math.max(1, duration);
        velocity = Math.min(maxSpeed, (velocity - frequency * momentum * elapsed) * decay);
        displayed = Math.min(target, displayed + maxSpeed * elapsed, Math.max(displayed, target + (offset + momentum * elapsed) * decay));
        if (target - displayed < 0.0005) {
          displayed = target;
          velocity = 0;
        }
      } else {
        displayed = Math.min(target, displayed + elapsed / Math.max(1, duration));
      }

      // Match screen density even after zooming or moving to another display.
      if (pixelRatio !== Math.max(window.devicePixelRatio || 1, 1)) resize();
      if (!width || !height) return;
      context.clearRect(0, 0, width, height);
      context.globalAlpha = 1;

      const motionTime = reducedMotion ? 0 : time;
      const rising = direction === "up";
      const clearing = mode === "clear";
      const travelSize = rising ? height : width;
      const crossSize = rising ? width : height;
      const ripple = Math.sin(displayed * Math.PI);
      const frontAt = (cross: number) => displayed * travelSize + ripple * (
        Math.sin(cross / crossSize * Math.PI * 2 + motionTime * 0.0012) * 14 +
        Math.sin(cross / crossSize * Math.PI * 4 - motionTime * 0.0008) * 5
      );
      const lineTo = (along: number, cross: number) => {
        context.lineTo(rising ? cross : along, rising ? height - along : cross);
      };
      const particleAt = (along: number, cross: number, radius: number, opacity: number, tint: string) => {
        drawParticle(rising ? cross : along, rising ? height - along : cross, radius, opacity, tint);
      };

      // A continuous surface behind the particles fills every part of the zone.
      context.fillStyle = colors.fill;
      context.beginPath();
      const anchor = clearing ? travelSize : 0;
      context.moveTo(rising ? 0 : anchor, rising ? height - anchor : 0);
      for (let cross = 0; cross < crossSize + 8; cross += 8) {
        const row = Math.min(cross, crossSize);
        lineTo(Math.max(0, Math.min(travelSize, frontAt(row))), row);
      }
      lineTo(anchor, crossSize);
      context.closePath();
      context.fill();

      const particleCount = rising ? Math.min(420, Math.max(140, Math.round(crossSize / 3))) : 140;
      const edgeCount = Math.round(particleCount * 0.65);
      for (let index = 0; index < particleCount && !(clearing && displayed >= 1); index++) {
        const particle = PARTICLES[index];
        const cross = particle.y * crossSize + Math.sin(motionTime * 0.001 + index) * 4;
        if (index < edgeCount) {
          const travel = (particle.x + motionTime * 0.0003 * particle.speed) % 1;
          const along = frontAt(cross) + (travel - 0.7) * 100;
          const opacity = Math.sin(travel * Math.PI) * 0.9;
          particleAt(along, cross, particle.radius, opacity, index % 4 === 0 ? colors.highlight : colors.particle);
        } else {
          const along = ((particle.x + motionTime * 0.000025 * particle.speed) % 1) * travelSize;
          if (clearing ? along > frontAt(cross) + 24 : along < frontAt(cross) - 24) {
            particleAt(along, cross, particle.radius, 0.24, colors.particle);
          }
        }
      }
      context.globalAlpha = 1;

      if (displayed >= 1 && onCompleteRef.current && !completionNotified) {
        completedAt ??= time;
        if (time - completedAt >= (reducedMotion ? 0 : completionDelay)) {
          completionNotified = true;
          onCompleteRef.current();
        }
      }
    };

    const observer = new ResizeObserver(resize);
    observer.observe(canvas);
    resize();
    // Paint immediately when changing to the clearing wave, avoiding a blank frame.
    draw(performance.now());
    return () => {
      cancelAnimationFrame(frame);
      observer.disconnect();
    };
  }, [direction, mode, duration, completionDelay]);

  return (
    <div className="upload-particle-wave" aria-hidden="true">
      <canvas ref={canvasRef} className="upload-particle-canvas" />
    </div>
  );
}
