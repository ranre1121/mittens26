/** Small, frame-rate-independent simulation for the waiting-room runner. */
export const RUN_HEIGHT = 176;
export const RUN_GROUND = 176;
export const RUNNER_WIDTH = 38;
const GRAVITY = 1800;
const JUMP_SPEED = 620;

export type RunPhase = "idle" | "running" | "paused" | "over";
export type Safe = { id: number; x: number; width: number; height: number; passed: boolean };
export type Run = {
  phase: RunPhase;
  width: number;
  height: number;
  velocity: number;
  distance: number;
  score: number;
  nextSafe: number;
  sequence: number;
  safes: Safe[];
};

export function runnerX(width: number) {
  return Math.min(100, Math.max(36, width * .1));
}

export function createRun(width = 960): Run {
  return { phase: "idle", width, height: 0, velocity: 0, distance: 0, score: 0, nextSafe: .65, sequence: 0, safes: [] };
}

export function startRun(run: Run) {
  Object.assign(run, createRun(run.width), {
    phase: "running",
    // Begin with a visible obstacle, instead of waiting for one offscreen.
    safes: [{ id: 0, x: run.width - 62, width: 38, height: 42, passed: false }],
    sequence: 1,
    nextSafe: 1 + Math.random() * 1.6,
  });
}

export function jump(run: Run) {
  if (run.phase !== "running" || run.height > 0) return;
  run.velocity = JUMP_SPEED;
}

export function resizeRun(run: Run, width: number) {
  const shift = runnerX(width) - runnerX(run.width);
  run.safes.forEach((safe) => { safe.x += shift; });
  run.width = width;
}

export function advanceRun(run: Run, seconds: number, random = Math.random) {
  if (run.phase !== "running" || !Number.isFinite(seconds) || seconds <= 0) return;
  // Cap catch-up after a stalled frame and substep collisions on slow devices.
  let remaining = Math.min(seconds, .05);
  while (remaining > 0 && run.phase === "running") {
    const dt = Math.min(remaining, 1 / 120);
    remaining -= dt;
    const speed = Math.min(410, 260 + run.score * 7);
    run.distance += speed * dt;
    run.height = Math.max(0, run.height + run.velocity * dt - GRAVITY * dt * dt / 2);
    run.velocity = run.height > 0 ? run.velocity - GRAVITY * dt : 0;
    run.nextSafe -= dt;
    if (run.nextSafe <= 0) {
      run.safes.push({ id: run.sequence++, x: run.width + 24, width: 32 + random() * 10, height: 32 + random() * 12, passed: false });
      // Vary gaps noticeably, while leaving time for a full jump and landing.
      run.nextSafe = 1 + random() * 1.6;
    }
    const x = runnerX(run.width);
    for (const safe of run.safes) {
      safe.x -= speed * dt;
      // A little inset makes the hitbox forgiving around the letter's curves.
      if (x + RUNNER_WIDTH - 5 > safe.x + 4 && x + 5 < safe.x + safe.width - 4 && run.height + 5 < safe.height) {
        run.phase = "over";
        return;
      }
      if (!safe.passed && safe.x + safe.width < x) {
        safe.passed = true;
        run.score++;
      }
    }
    run.safes = run.safes.filter((safe) => safe.x + safe.width > -20);
  }
}
