import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { afterEach, describe, expect, it } from "vitest";
import { buildGuideContext, normalizeGuideOutputDir, saveGuideArtifacts } from "./guide.js";
import type { ExperimentRecord } from "./types.js";

describe("science lab guides", () => {
  const tempDirs: string[] = [];

  afterEach(async () => {
    await Promise.all(
      tempDirs.splice(0).map((dir) => fs.rm(dir, { recursive: true, force: true })),
    );
  });

  it("returns the required checklist and image prompt seed", () => {
    const context = buildGuideContext({
      experiment: experiment(),
      inventory: [],
    });

    expect(context.guideChecklist).toContain("Prediction before explanation");
    expect(context.styleRules).toContain("Avoid worksheet vibes");
    expect(context.personalityContract.parentVoice).toContain("warm coach");
    expect(context.imagePromptSeed).toContain("Ice Cream in a Bag");
  });

  it("saves Markdown artifacts under a safe relative output directory", async () => {
    const dir = await fs.mkdtemp(path.join(os.tmpdir(), "openclaw-science-guide-"));
    tempDirs.push(dir);

    const record = await saveGuideArtifacts({
      experimentId: "ice-cream-bag",
      markdown: "# Parent Guide\n",
      imagePromptMarkdown: "# Image Prompt\n",
      workspaceRoot: dir,
      now: "2026-07-05T00:00:00.000Z",
    });

    expect(record.guidePath).toBe("outputs/science_lab_guides/ice-cream-bag-guide.md");
    expect(record.imagePromptPath).toBe(
      "outputs/science_lab_guides/ice-cream-bag-guide-image-prompt.md",
    );
    await expect(fs.readFile(path.join(dir, record.guidePath), "utf8")).resolves.toContain(
      "# Parent Guide",
    );
  });

  it("rejects absolute and parent-relative output paths", () => {
    expect(() => normalizeGuideOutputDir("/tmp/science")).toThrow(/relative/u);
    expect(() => normalizeGuideOutputDir("../science")).toThrow(/inside/u);
  });
});

function experiment(): ExperimentRecord {
  return {
    version: 1,
    id: "ice-cream-bag",
    title: "Ice Cream in a Bag",
    waitingTime: "low",
    concepts: ["freezing point"],
    materials: [{ name: "milk" }, { name: "rock salt" }],
    visualExcitement: "high",
    safetyNotes: ["Adult handles food safety and cleanup."],
    updatedAt: "2026-07-05T00:00:00.000Z",
  };
}
