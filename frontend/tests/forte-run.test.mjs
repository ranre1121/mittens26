import { test } from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import vm from "node:vm";
import ts from "typescript";

const exportsObject = {};
const source = fs.readFileSync(new URL("../src/lib/forte-run.ts", import.meta.url), "utf8");
vm.runInNewContext(ts.transpileModule(source, {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
}).outputText, { exports: exportsObject });
const { createRun, startRun, advanceRun, jump, resizeRun, runnerX } = exportsObject;

function move(run, seconds, fps = 60) {
  for (let frame = 0; frame < Math.round(seconds * fps); frame++) advanceRun(run, 1 / fps, () => .5);
}

function encounter(width = 960) {
  const run = createRun(width);
  startRun(run);
  run.nextSafe = 99;
  run.safes = [{ id: 1, x: runnerX(width) + 110, width: 40, height: 44, passed: false }];
  return run;
}

test("a grounded runner hits the safe and the score stops", () => {
  const run = encounter();
  move(run, .6);
  assert.equal(run.phase, "over");
  assert.equal(run.score, 0);
  const distance = run.distance;
  move(run, 10);
  assert.equal(run.distance, distance);
});

test("one timed jump clears a safe and earns exactly one point", () => {
  for (const width of [280, 560, 1200]) {
    const run = encounter(width);
    jump(run);
    move(run, .8);
    assert.equal(run.phase, "running");
    assert.equal(run.score, 1);
    assert.equal(run.height, 0);
    move(run, 1);
    assert.equal(run.score, 1);
  }
});

test("holding jump does not grant an extra midair jump", () => {
  const run = encounter();
  jump(run);
  move(run, .2);
  const velocity = run.velocity;
  jump(run);
  assert.equal(run.velocity, velocity);
  move(run, .6);
  assert.equal(run.height, 0);
});

test("jump and collision outcomes agree at 30, 60 and 120 fps", () => {
  const outcomes = [30, 60, 120].map((fps) => {
    const run = encounter();
    jump(run);
    move(run, .4, fps);
    assert.ok(run.height > 90 && run.height < 110);
    move(run, .4, fps);
    return [run.phase, run.score, run.height];
  });
  assert.deepEqual(outcomes[0], outcomes[1]);
  assert.deepEqual(outcomes[1], outcomes[2]);
});

test("pause freezes the world; resizing preserves obstacle distance; restart clears the run", () => {
  const run = encounter();
  jump(run);
  move(run, .2);
  run.phase = "paused";
  const before = JSON.stringify(run);
  move(run, 20);
  assert.equal(JSON.stringify(run), before);
  const gap = run.safes[0].x - runnerX(run.width);
  resizeRun(run, 320);
  assert.equal(run.safes[0].x - runnerX(run.width), gap);
  run.phase = "running";
  move(run, .6);
  assert.equal(run.score, 1);
  startRun(run);
  assert.equal(run.width, 320);
  assert.equal(run.score, 0);
  assert.equal(run.height, 0);
  assert.equal(run.safes.length, 1);
  assert.ok(run.safes[0].x > runnerX(run.width));
  assert.ok(run.safes[0].x + run.safes[0].width <= run.width, "The opening safe is visible immediately on restart");
});

test("bad frame times cannot teleport the player; each safe remains jumpable at maximum speed", () => {
  const run = createRun();
  startRun(run);
  advanceRun(run, NaN);
  advanceRun(run, -1);
  assert.equal(run.distance, 0);
  advanceRun(run, 500);
  assert.ok(run.distance <= 14);
  const fast = encounter();
  fast.score = 100;
  jump(fast);
  move(fast, .8);
  assert.equal(fast.phase, "running");
  assert.equal(fast.score, 101);
});
