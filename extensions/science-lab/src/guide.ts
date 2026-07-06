import fs from "node:fs/promises";
import path from "node:path";
import { classifyMaterialsForExperiments } from "./materials.js";
import {
  DEFAULT_GUIDE_OUTPUT_DIR,
  type ExperimentRecord,
  type GuideContext,
  type InventoryEntry,
  type SavedGuideRecord,
  type ScienceLabProfile,
} from "./types.js";

export const GUIDE_SECTION_CHECKLIST = [
  "Parent overview",
  "What we are wondering",
  "Prediction before explanation",
  "Material plan",
  "Safety and cleanup",
  "Parent prep video searches",
  "Kid script",
  "During experiment coaching",
  "Simple science explanation",
  "Real-world connections",
  "Curious reset ideas",
  "Reflection questions",
  "Conversation quiz",
  "Science journal prompt",
  "Image prompt",
] as const;

export function buildGuideContext(params: {
  experiment: ExperimentRecord;
  inventory: readonly InventoryEntry[];
  profile?: ScienceLabProfile;
}): GuideContext {
  const targetAgeRange = params.profile?.targetAgeRange ?? { min: 4, max: 7 };
  const materialPlan = classifyMaterialsForExperiments({
    experiments: [params.experiment],
    inventory: params.inventory,
  });
  const conceptText =
    params.experiment.concepts.length > 0
      ? params.experiment.concepts.join(", ")
      : "observation, prediction, and testing";

  return {
    experiment: params.experiment,
    materialPlan,
    guideChecklist: [...GUIDE_SECTION_CHECKLIST],
    personalityContract: {
      parentVoice: "warm coach: calm, prepared, practical, encouraging, concise",
      kidScriptVoice:
        "everyday magic: ordinary materials can feel surprising while the science stays clear",
      failureStyle: "curious reset: say what did we notice and what could we change next",
      safetyTone:
        "warm but firm for hazards, allergens, heat, glass, small parts, food safety, and cleanup",
    },
    styleRules: [
      "Prediction before explanation",
      "Ask before telling",
      "Celebrate guesses",
      "Use child words first",
      "End with wonder and a next question",
      "Avoid worksheet vibes",
      "Avoid generic Life Skills sections",
      "Avoid long lectures",
      "Avoid over-fantasy storytelling that hides the science",
    ],
    videoSearchQueries: [
      `${params.experiment.title} science experiment explanation for kids`,
      `${conceptText} science for kids`,
      `${params.experiment.title} parent setup safety tips`,
    ],
    realWorldPrompts: [
      `Where do we see ${conceptText} in the kitchen, bathroom, backyard, or weather?`,
      "What job would use this idea on purpose?",
      "What would change if we used more, less, warmer, colder, bigger, or smaller materials?",
    ],
    imagePromptSeed:
      `Create a friendly visual explainer for ages ${targetAgeRange.min}-${targetAgeRange.max}: ` +
      `${params.experiment.title}. Show the key materials, the before and after states, ` +
      `arrows for what changes, and one short kid-friendly takeaway about ${conceptText}.`,
  };
}

export async function saveGuideArtifacts(params: {
  experimentId: string;
  markdown: string;
  imagePromptMarkdown?: string;
  outputDir?: string;
  workspaceRoot?: string;
  now: string;
}): Promise<SavedGuideRecord> {
  const markdown = params.markdown.trim();
  if (!markdown) {
    throw new Error("science_lab_save_guide requires non-empty Markdown");
  }

  const outputDir = normalizeGuideOutputDir(params.outputDir);
  const stem = safeFilename(`${params.experimentId}-guide`);
  const workspaceRoot = params.workspaceRoot ?? process.cwd();
  const outputRoot = path.resolve(workspaceRoot, outputDir);
  await fs.mkdir(outputRoot, { recursive: true });

  const guidePath = toPosixPath(path.join(outputDir, `${stem}.md`));
  await fs.writeFile(path.resolve(workspaceRoot, guidePath), `${markdown}\n`, "utf8");

  let imagePromptPath: string | undefined;
  const imagePromptMarkdown = params.imagePromptMarkdown?.trim();
  if (imagePromptMarkdown) {
    imagePromptPath = toPosixPath(path.join(outputDir, `${stem}-image-prompt.md`));
    await fs.writeFile(
      path.resolve(workspaceRoot, imagePromptPath),
      `${imagePromptMarkdown}\n`,
      "utf8",
    );
  }

  return {
    version: 1,
    experimentId: params.experimentId,
    guidePath,
    ...(imagePromptPath ? { imagePromptPath } : {}),
    savedAt: params.now,
  };
}

export function normalizeGuideOutputDir(outputDir: string | undefined): string {
  const raw = outputDir?.trim() || DEFAULT_GUIDE_OUTPUT_DIR;
  if (path.isAbsolute(raw)) {
    throw new Error("science_lab_save_guide outputDir must be relative");
  }
  const normalized = path.normalize(raw);
  const parts = normalized.split(/[\\/]+/u);
  if (
    normalized === "." ||
    parts.some((part) => part === ".." || part === "" || part.includes("\0"))
  ) {
    throw new Error("science_lab_save_guide outputDir must stay inside the workspace");
  }
  return toPosixPath(normalized);
}

function safeFilename(value: string): string {
  const stem = value
    .toLowerCase()
    .replace(/[^a-z0-9]+/gu, "-")
    .replace(/^-|-$/gu, "");
  return stem || "science-lab-guide";
}

function toPosixPath(value: string): string {
  return value.replace(/\\/gu, "/").split(path.sep).join("/");
}
