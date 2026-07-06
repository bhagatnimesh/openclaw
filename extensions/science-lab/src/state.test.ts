import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import type { OpenKeyedStoreOptions } from "openclaw/plugin-sdk/plugin-state-runtime";
import {
  createPluginStateKeyedStoreForTests,
  resetPluginStateStoreForTests,
} from "openclaw/plugin-sdk/plugin-state-test-runtime";
import { afterEach, describe, expect, it } from "vitest";
import { createScienceLabState } from "./state.js";

describe("science lab state", () => {
  const tempDirs: string[] = [];

  afterEach(async () => {
    resetPluginStateStoreForTests();
    await Promise.all(
      tempDirs.splice(0).map((dir) => fs.rm(dir, { recursive: true, force: true })),
    );
  });

  it("persists profile, experiments, inventory, and progress in plugin state", async () => {
    const dir = await fs.mkdtemp(path.join(os.tmpdir(), "openclaw-science-lab-"));
    tempDirs.push(dir);
    const openKeyedStore = <T>(options: OpenKeyedStoreOptions) =>
      createPluginStateKeyedStoreForTests<T>("science-lab", {
        ...options,
        env: { ...process.env, OPENCLAW_STATE_DIR: dir },
      });
    const state = createScienceLabState(openKeyedStore);

    await state.saveProfile({
      version: 1,
      children: [{ name: "Mira", age: 7, interests: ["ice"] }],
      targetAgeRange: { min: 4, max: 7 },
      updatedAt: "2026-07-05T00:00:00.000Z",
    });
    await state.upsertExperiments([
      {
        version: 1,
        id: "ice-cream-bag",
        title: "Ice Cream in a Bag",
        waitingTime: "low",
        concepts: ["freezing point"],
        materials: [{ name: "rock salt" }],
        visualExcitement: "high",
        safetyNotes: [],
        updatedAt: "2026-07-05T00:00:00.000Z",
      },
    ]);
    await state.upsertInventory([
      {
        version: 1,
        materialId: "rock-salt",
        displayName: "Rock salt",
        status: "missing",
        lastUpdated: "2026-07-05T00:00:00.000Z",
      },
    ]);
    await state.upsertProgress({
      version: 1,
      experimentId: "ice-cream-bag",
      status: "planned",
      childFeedback: [],
      childQuestions: [],
      preferenceTags: [],
      updatedAt: "2026-07-05T00:00:00.000Z",
    });

    const reloaded = createScienceLabState(openKeyedStore);

    await expect(reloaded.getProfile()).resolves.toMatchObject({
      children: [{ name: "Mira", age: 7, interests: ["ice"] }],
    });
    await expect(reloaded.listExperiments()).resolves.toHaveLength(1);
    await expect(reloaded.listInventory()).resolves.toMatchObject([
      { materialId: "rock-salt", status: "missing" },
    ]);
    await expect(reloaded.listProgress()).resolves.toMatchObject([
      { experimentId: "ice-cream-bag", status: "planned" },
    ]);
  });
});
