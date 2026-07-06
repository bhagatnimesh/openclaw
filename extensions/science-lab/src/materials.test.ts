import { describe, expect, it } from "vitest";
import { parseInventoryText } from "./inventory.js";
import {
  amazonSearchUrl,
  canonicalizeMaterial,
  classifyMaterialsForExperiments,
} from "./materials.js";
import type { ExperimentRecord, InventoryEntry } from "./types.js";

describe("science lab materials", () => {
  it("canonicalizes common household aliases", () => {
    expect(canonicalizeMaterial("ziploc bags")).toMatchObject({
      materialId: "zip-top-bags",
      displayName: "Zip-top bags",
    });
    expect(canonicalizeMaterial("Alka Seltzer")).toMatchObject({
      materialId: "effervescent-antacid-tablets",
      displayName: "Effervescent antacid tablets",
    });
  });

  it("builds Amazon search URLs without claiming product selection", () => {
    expect(amazonSearchUrl("child safety goggles science")).toBe(
      "https://www.amazon.com/s?k=child+safety+goggles+science",
    );
  });

  it("parses live inventory text into statuses", () => {
    expect(
      parseInventoryText(
        "We have salt, food coloring and zip bags. Out of rock salt. Low on paper towels.",
      ),
    ).toEqual([
      { name: "salt", status: "have" },
      { name: "food coloring", status: "have" },
      { name: "zip bags", status: "have" },
      { name: "rock salt", status: "missing" },
      { name: "paper towels", status: "low" },
    ]);
  });

  it("groups material plans by inventory and buying need", () => {
    const experiment = fixtureExperiment({
      materials: [
        { name: "zip bags" },
        { name: "rock salt" },
        { name: "milk" },
        { name: "safety goggles" },
      ],
    });
    const inventory: InventoryEntry[] = [
      {
        version: 1,
        materialId: "zip-top-bags",
        displayName: "Zip-top bags",
        status: "have",
        lastUpdated: "2026-07-05T00:00:00.000Z",
      },
      {
        version: 1,
        materialId: "milk",
        displayName: "Milk",
        status: "unknown",
        lastUpdated: "2026-07-05T00:00:00.000Z",
      },
    ];

    const plan = classifyMaterialsForExperiments({ experiments: [experiment], inventory });

    expect(plan.alreadyInHomeInventory.map((item) => item.materialId)).toEqual(["zip-top-bags"]);
    expect(plan.pleaseConfirm.map((item) => item.materialId)).toEqual(["milk"]);
    expect(plan.recommendedAmazonOrder.map((item) => item.materialId)).toEqual([
      "rock-salt",
      "safety-goggles",
    ]);
    expect(plan.recommendedAmazonOrder[0]?.amazonSearchLabel).toBe("Amazon search link");
  });
});

function fixtureExperiment(params: { materials: ExperimentRecord["materials"] }): ExperimentRecord {
  return {
    version: 1,
    id: "ice-cream-bag",
    title: "Ice Cream in a Bag",
    waitingTime: "low",
    concepts: ["freezing point"],
    materials: params.materials,
    visualExcitement: "high",
    safetyNotes: [],
    libraryOrder: 1,
    updatedAt: "2026-07-05T00:00:00.000Z",
  };
}
