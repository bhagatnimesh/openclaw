import { describe, expect, it } from "vitest";
import { planScienceLabExperiments } from "./planner.js";
import type { ExperimentProgress, ExperimentRecord, InventoryEntry } from "./types.js";

describe("science lab planner", () => {
  it("skips completed experiments and honors replacement exclusions", () => {
    const experiments = [
      experiment("already-done", "Already Done", { order: 1 }),
      experiment("replace-me", "Replace Me", { order: 2 }),
      experiment("next-good", "Next Good", { order: 3 }),
      experiment("next-second", "Next Second", { order: 4 }),
    ];
    const progress: ExperimentProgress[] = [
      progressRecord("already-done", "completed"),
      progressRecord("replace-me", "planned"),
    ];

    const plan = planScienceLabExperiments({
      experiments,
      progress,
      inventory: [],
      count: 2,
      replaceIds: ["replace-me"],
    });

    expect(plan.selected.map((entry) => entry.experiment.id)).toEqual(["next-good", "next-second"]);
    expect(plan.skippedExperimentIds).toEqual(["already-done", "replace-me"]);
  });

  it("allows explicit include to override completed status", () => {
    const plan = planScienceLabExperiments({
      experiments: [experiment("repeat", "Repeat Favorite", { order: 1 })],
      progress: [progressRecord("repeat", "completed")],
      inventory: [],
      count: 1,
      includeIds: ["repeat"],
    });

    expect(plan.selected[0]?.experiment.id).toBe("repeat");
    expect(plan.selected[0]?.reasons).toContain("explicitly included");
  });

  it("uses child feedback tags when ranking candidates", () => {
    const experiments = [
      experiment("slow-magnet", "Magnet Sort", {
        concepts: ["magnetism"],
        visualExcitement: "low",
        waitingTime: "medium",
        order: 1,
      }),
      experiment("bright-color", "Color Volcano", {
        concepts: ["color", "reaction"],
        visualExcitement: "low",
        waitingTime: "high",
        order: 2,
      }),
    ];
    const progress = [progressRecord("old", "completed", ["magnetism", "magnet", "magnets"])];
    const inventory: InventoryEntry[] = [
      {
        version: 1,
        materialId: "magnets",
        displayName: "Magnets",
        status: "have",
        lastUpdated: "2026-07-05T00:00:00.000Z",
      },
    ];

    const plan = planScienceLabExperiments({
      experiments,
      progress,
      inventory,
      count: 1,
    });

    expect(plan.selected[0]?.experiment.id).toBe("slow-magnet");
    expect(plan.selected[0]?.reasons).toContain("matches child feedback");
  });
});

function experiment(
  id: string,
  title: string,
  options: {
    concepts?: string[];
    visualExcitement?: "low" | "medium" | "high";
    waitingTime?: "low" | "medium" | "high";
    order: number;
  },
): ExperimentRecord {
  return {
    version: 1,
    id,
    title,
    waitingTime: options.waitingTime ?? "low",
    concepts: options.concepts ?? ["observation"],
    materials: [{ name: id === "slow-magnet" ? "magnets" : "water" }],
    visualExcitement: options.visualExcitement ?? "high",
    safetyNotes: [],
    libraryOrder: options.order,
    updatedAt: "2026-07-05T00:00:00.000Z",
  };
}

function progressRecord(
  experimentId: string,
  status: ExperimentProgress["status"],
  preferenceTags: string[] = [],
): ExperimentProgress {
  return {
    version: 1,
    experimentId,
    status,
    childFeedback: [],
    childQuestions: [],
    preferenceTags,
    updatedAt: "2026-07-05T00:00:00.000Z",
  };
}
