import { defineToolPlugin } from "openclaw/plugin-sdk/tool-plugin";
import { Type, type Static } from "typebox";
import { buildGuideContext, saveGuideArtifacts } from "./src/guide.js";
import { createInventoryEntry, parseInventoryText } from "./src/inventory.js";
import { normalizeMaterialId } from "./src/materials.js";
import { planScienceLabExperiments } from "./src/planner.js";
import { createScienceLabState } from "./src/state.js";
import type {
  ChildProfile,
  ExperimentMaterial,
  ExperimentProgress,
  ExperimentRecord,
  ExperimentStatus,
  InventoryEntry,
  MaterialStatus,
  ScienceLabProfile,
} from "./src/types.js";

const MaterialStatusSchema = Type.Union([
  Type.Literal("have"),
  Type.Literal("missing"),
  Type.Literal("low"),
  Type.Literal("unknown"),
]);

const ExperimentStatusSchema = Type.Union([
  Type.Literal("planned"),
  Type.Literal("completed"),
  Type.Literal("skipped"),
]);

const TimeBandSchema = Type.Union([
  Type.Literal("low"),
  Type.Literal("medium"),
  Type.Literal("high"),
]);

const ChildProfileSchema = Type.Object(
  {
    name: Type.String({ minLength: 1 }),
    age: Type.Optional(Type.Integer({ minimum: 0, maximum: 18 })),
    interests: Type.Optional(Type.Array(Type.String(), { maxItems: 20 })),
  },
  { additionalProperties: false },
);

const AgeRangeSchema = Type.Object(
  {
    min: Type.Integer({ minimum: 0, maximum: 18 }),
    max: Type.Integer({ minimum: 0, maximum: 18 }),
  },
  { additionalProperties: false },
);

const ExperimentMaterialSchema = Type.Object(
  {
    name: Type.String({ minLength: 1 }),
    quantity: Type.Optional(Type.String()),
    notes: Type.Optional(Type.String()),
  },
  { additionalProperties: false },
);

const ExperimentInputSchema = Type.Object(
  {
    id: Type.Optional(Type.String()),
    title: Type.String({ minLength: 1 }),
    sourceLabel: Type.Optional(Type.String()),
    sourceRef: Type.Optional(Type.String()),
    category: Type.Optional(Type.String()),
    difficulty: Type.Optional(Type.String()),
    prepMinutes: Type.Optional(Type.Integer({ minimum: 0 })),
    activeMinutes: Type.Optional(Type.Integer({ minimum: 0 })),
    waitingTime: Type.Optional(TimeBandSchema),
    messLevel: Type.Optional(TimeBandSchema),
    ageRange: Type.Optional(AgeRangeSchema),
    concepts: Type.Optional(Type.Array(Type.String(), { maxItems: 40 })),
    materials: Type.Array(ExperimentMaterialSchema, { minItems: 1, maxItems: 80 }),
    visualExcitement: Type.Optional(TimeBandSchema),
    safetyNotes: Type.Optional(Type.Array(Type.String(), { maxItems: 40 })),
    sourceNotes: Type.Optional(Type.String()),
    libraryOrder: Type.Optional(Type.Integer({ minimum: 0 })),
  },
  { additionalProperties: false },
);

const ProfileParamsSchema = Type.Object(
  {
    children: Type.Optional(Type.Array(ChildProfileSchema, { maxItems: 16 })),
    targetAgeRange: Type.Optional(AgeRangeSchema),
  },
  { additionalProperties: false },
);

const ImportExperimentsParamsSchema = Type.Object(
  {
    experiments: Type.Array(ExperimentInputSchema, { minItems: 1, maxItems: 200 }),
  },
  { additionalProperties: false },
);

const InventoryUpdateSchema = Type.Object(
  {
    name: Type.String({ minLength: 1 }),
    status: MaterialStatusSchema,
    quantity: Type.Optional(Type.String()),
    notes: Type.Optional(Type.String()),
  },
  { additionalProperties: false },
);

const InventoryParamsSchema = Type.Object(
  {
    updates: Type.Optional(Type.Array(InventoryUpdateSchema, { maxItems: 300 })),
    text: Type.Optional(Type.String()),
  },
  { additionalProperties: false },
);

const PlanParamsSchema = Type.Object(
  {
    count: Type.Optional(Type.Integer({ minimum: 1, maximum: 50 })),
    includeIds: Type.Optional(Type.Array(Type.String(), { maxItems: 50 })),
    excludeIds: Type.Optional(Type.Array(Type.String(), { maxItems: 200 })),
    replaceIds: Type.Optional(Type.Array(Type.String(), { maxItems: 200 })),
  },
  { additionalProperties: false },
);

const ExperimentStatusParamsSchema = Type.Object(
  {
    experimentId: Type.String({ minLength: 1 }),
    status: Type.Optional(ExperimentStatusSchema),
    feedback: Type.Optional(Type.String()),
    childFeedback: Type.Optional(Type.Array(Type.String(), { maxItems: 40 })),
    childQuestions: Type.Optional(Type.Array(Type.String(), { maxItems: 40 })),
    preferenceTags: Type.Optional(Type.Array(Type.String(), { maxItems: 40 })),
    parentNotes: Type.Optional(Type.String()),
  },
  { additionalProperties: false },
);

const GuideContextParamsSchema = Type.Object(
  {
    experimentId: Type.Optional(Type.String()),
    title: Type.Optional(Type.String()),
  },
  { additionalProperties: false },
);

const SaveGuideParamsSchema = Type.Object(
  {
    experimentId: Type.String({ minLength: 1 }),
    markdown: Type.String({ minLength: 1 }),
    imagePromptMarkdown: Type.Optional(Type.String()),
    outputDir: Type.Optional(Type.String()),
  },
  { additionalProperties: false },
);

type ProfileParams = Static<typeof ProfileParamsSchema>;
type ImportExperimentsParams = Static<typeof ImportExperimentsParamsSchema>;
type InventoryParams = Static<typeof InventoryParamsSchema>;
type PlanParams = Static<typeof PlanParamsSchema>;
type ExperimentStatusParams = Static<typeof ExperimentStatusParamsSchema>;
type GuideContextParams = Static<typeof GuideContextParamsSchema>;
type SaveGuideParams = Static<typeof SaveGuideParamsSchema>;

export default defineToolPlugin({
  id: "science-lab",
  name: "Science Lab",
  description: "Plan private home science experiments, materials, guides, and reflection.",
  tools: (tool) => [
    tool({
      name: "science_lab_profile",
      label: "Science Lab Profile",
      description: "Set or list child profiles, interests, and the default target age band.",
      parameters: ProfileParamsSchema,
      optional: true,
      async execute(params, _config, context) {
        const state = createScienceLabState(context.api.runtime.state.openKeyedStore);
        const now = new Date().toISOString();
        const existing = await state.getProfile();
        if (!hasProfileUpdates(params)) {
          return {
            profile: existing ?? defaultProfile(now),
          };
        }

        const profile = normalizeProfile(params, existing, now);
        await state.saveProfile(profile);
        return { profile };
      },
    }),
    tool({
      name: "science_lab_import_experiments",
      label: "Import Science Lab Experiments",
      description:
        "Upsert private structured experiment records extracted from user-provided images or text.",
      parameters: ImportExperimentsParamsSchema,
      optional: true,
      async execute(params, _config, context) {
        const state = createScienceLabState(context.api.runtime.state.openKeyedStore);
        const now = new Date().toISOString();
        const records = params.experiments.map((experiment, index) =>
          normalizeExperimentRecord(experiment, now, index),
        );
        await state.upsertExperiments(records);
        return {
          imported: records.length,
          experiments: records.map((record) => ({
            id: record.id,
            title: record.title,
            materials: record.materials.map((material) => material.name),
          })),
        };
      },
    }),
    tool({
      name: "science_lab_inventory",
      label: "Science Lab Inventory",
      description: "List or update material inventory statuses: have, missing, low, or unknown.",
      parameters: InventoryParamsSchema,
      optional: true,
      async execute(params, _config, context) {
        const state = createScienceLabState(context.api.runtime.state.openKeyedStore);
        const now = new Date().toISOString();
        const structuredUpdates = params.updates ?? [];
        const textUpdates = params.text ? parseInventoryText(params.text) : [];
        const updates = [...structuredUpdates, ...textUpdates];
        const entries = updates.map((update) =>
          createInventoryEntry(
            {
              name: update.name,
              status: update.status as MaterialStatus,
              ...(update.quantity ? { quantity: update.quantity } : {}),
              ...(update.notes ? { notes: update.notes } : {}),
            },
            now,
          ),
        );
        if (entries.length > 0) {
          await state.upsertInventory(entries);
        }
        return {
          updated: entries,
          inventory: await state.listInventory(),
        };
      },
    }),
    tool({
      name: "science_lab_plan",
      label: "Plan Science Lab Experiments",
      description:
        "Select the next experiments, defaulting to four, with include, exclude, and replace controls.",
      parameters: PlanParamsSchema,
      optional: true,
      async execute(params, _config, context) {
        const state = createScienceLabState(context.api.runtime.state.openKeyedStore);
        const [experiments, progress, inventory, profile] = await Promise.all([
          state.listExperiments(),
          state.listProgress(),
          state.listInventory(),
          state.getProfile(),
        ]);
        const plan = planScienceLabExperiments({
          experiments,
          progress,
          inventory,
          ...(profile ? { profile } : {}),
          count: params.count ?? 4,
          includeIds: params.includeIds ?? [],
          excludeIds: params.excludeIds ?? [],
          replaceIds: params.replaceIds ?? [],
        });
        return {
          selectedExperiments: plan.selected.map((entry) => ({
            id: entry.experiment.id,
            title: entry.experiment.title,
            score: entry.score,
            reasons: entry.reasons,
            concepts: entry.experiment.concepts,
          })),
          skippedExperimentIds: plan.skippedExperimentIds,
          materialPlan: plan.materialPlan,
        };
      },
    }),
    tool({
      name: "science_lab_experiment_status",
      label: "Science Lab Experiment Status",
      description: "Mark an experiment planned, completed, or skipped and record feedback.",
      parameters: ExperimentStatusParamsSchema,
      optional: true,
      async execute(params, _config, context) {
        const state = createScienceLabState(context.api.runtime.state.openKeyedStore);
        const now = new Date().toISOString();
        const existing = await state.getProgress(params.experimentId);
        const record = mergeProgress(existing, params, now);
        await state.upsertProgress(record);
        return { progress: record };
      },
    }),
    tool({
      name: "science_lab_guide_context",
      label: "Science Lab Guide Context",
      description:
        "Return experiment facts, material plan, guide checklist, searches, prompts, and image seed.",
      parameters: GuideContextParamsSchema,
      optional: true,
      async execute(params, _config, context) {
        const state = createScienceLabState(context.api.runtime.state.openKeyedStore);
        const [experiments, inventory, profile, progress] = await Promise.all([
          state.listExperiments(),
          state.listInventory(),
          state.getProfile(),
          state.listProgress(),
        ]);
        const experiment = selectGuideExperiment({ params, experiments, progress, inventory });
        if (!experiment) {
          throw new Error("No science lab experiment is available for guide context");
        }
        return buildGuideContext({
          experiment,
          inventory,
          ...(profile ? { profile } : {}),
        });
      },
    }),
    tool({
      name: "science_lab_save_guide",
      label: "Save Science Lab Guide",
      description:
        "Save a final Markdown guide and optional image prompt Markdown under a safe output path.",
      parameters: SaveGuideParamsSchema,
      optional: true,
      async execute(params, _config, context) {
        const state = createScienceLabState(context.api.runtime.state.openKeyedStore);
        const experiment = await state.getExperiment(params.experimentId);
        if (!experiment) {
          throw new Error(`Unknown science lab experiment: ${params.experimentId}`);
        }
        const now = new Date().toISOString();
        const record = await saveGuideArtifacts({
          experimentId: experiment.id,
          markdown: params.markdown,
          ...(params.imagePromptMarkdown
            ? { imagePromptMarkdown: params.imagePromptMarkdown }
            : {}),
          ...(params.outputDir ? { outputDir: params.outputDir } : {}),
          now,
        });
        await state.saveGuide(record);
        return { saved: record };
      },
    }),
  ],
});

function hasProfileUpdates(params: ProfileParams): boolean {
  return params.children !== undefined || params.targetAgeRange !== undefined;
}

function defaultProfile(now: string): ScienceLabProfile {
  return {
    version: 1,
    children: [],
    targetAgeRange: { min: 4, max: 7 },
    updatedAt: now,
  };
}

function normalizeProfile(
  params: ProfileParams,
  existing: ScienceLabProfile | undefined,
  now: string,
): ScienceLabProfile {
  const base = existing ?? defaultProfile(now);
  const targetAgeRange = params.targetAgeRange
    ? normalizeAgeRange(params.targetAgeRange.min, params.targetAgeRange.max)
    : base.targetAgeRange;
  return {
    version: 1,
    children: params.children ? params.children.map(normalizeChildProfile) : base.children,
    targetAgeRange,
    updatedAt: now,
  };
}

function normalizeChildProfile(child: Static<typeof ChildProfileSchema>): ChildProfile {
  return {
    name: child.name.trim(),
    ...(child.age !== undefined ? { age: child.age } : {}),
    interests: normalizeStringList(child.interests ?? []),
  };
}

function normalizeExperimentRecord(
  input: ImportExperimentsParams["experiments"][number],
  now: string,
  index: number,
): ExperimentRecord {
  const title = input.title.trim();
  const id = safeId(input.id ?? title);
  const materials = input.materials.map(normalizeExperimentMaterial);
  return {
    version: 1,
    id,
    title,
    ...(input.sourceLabel?.trim() ? { sourceLabel: input.sourceLabel.trim() } : {}),
    ...(input.sourceRef?.trim() ? { sourceRef: input.sourceRef.trim() } : {}),
    ...(input.category?.trim() ? { category: input.category.trim() } : {}),
    ...(input.difficulty?.trim() ? { difficulty: input.difficulty.trim() } : {}),
    ...(input.prepMinutes !== undefined ? { prepMinutes: input.prepMinutes } : {}),
    ...(input.activeMinutes !== undefined ? { activeMinutes: input.activeMinutes } : {}),
    waitingTime: input.waitingTime ?? "medium",
    ...(input.messLevel ? { messLevel: input.messLevel } : {}),
    ...(input.ageRange
      ? { ageRange: normalizeAgeRange(input.ageRange.min, input.ageRange.max) }
      : {}),
    concepts: normalizeStringList(input.concepts ?? []),
    materials,
    visualExcitement: input.visualExcitement ?? "medium",
    safetyNotes: normalizeStringList(input.safetyNotes ?? []),
    ...(input.sourceNotes?.trim() ? { sourceNotes: input.sourceNotes.trim() } : {}),
    libraryOrder: input.libraryOrder ?? index,
    updatedAt: now,
  };
}

function normalizeExperimentMaterial(
  material: Static<typeof ExperimentMaterialSchema>,
): ExperimentMaterial {
  return {
    name: material.name.trim(),
    ...(material.quantity?.trim() ? { quantity: material.quantity.trim() } : {}),
    ...(material.notes?.trim() ? { notes: material.notes.trim() } : {}),
  };
}

function normalizeAgeRange(min: number, max: number): { min: number; max: number } {
  return min <= max ? { min, max } : { min: max, max: min };
}

function normalizeStringList(values: readonly string[]): string[] {
  return [...new Set(values.map((value) => value.trim()).filter(Boolean))];
}

function safeId(value: string): string {
  return normalizeMaterialId(value).slice(0, 96) || "science-lab-experiment";
}

function mergeProgress(
  existing: ExperimentProgress | undefined,
  params: ExperimentStatusParams,
  now: string,
): ExperimentProgress {
  const status = (params.status ?? existing?.status ?? "planned") as ExperimentStatus;
  return {
    version: 1,
    experimentId: params.experimentId,
    status,
    ...(status === "planned" ? { plannedAt: existing?.plannedAt ?? now } : {}),
    ...(status === "completed" ? { completedAt: now } : {}),
    ...(status === "skipped" ? { skippedAt: now } : {}),
    ...(params.feedback?.trim()
      ? { feedback: params.feedback.trim() }
      : existing?.feedback
        ? { feedback: existing.feedback }
        : {}),
    childFeedback: mergeStringLists(existing?.childFeedback ?? [], params.childFeedback ?? []),
    childQuestions: mergeStringLists(existing?.childQuestions ?? [], params.childQuestions ?? []),
    preferenceTags: mergeStringLists(existing?.preferenceTags ?? [], params.preferenceTags ?? []),
    ...(params.parentNotes?.trim()
      ? { parentNotes: params.parentNotes.trim() }
      : existing?.parentNotes
        ? { parentNotes: existing.parentNotes }
        : {}),
    updatedAt: now,
  };
}

function mergeStringLists(existing: readonly string[], incoming: readonly string[]): string[] {
  return normalizeStringList([...existing, ...incoming]);
}

function selectGuideExperiment(params: {
  params: GuideContextParams;
  experiments: readonly ExperimentRecord[];
  progress: readonly ExperimentProgress[];
  inventory: readonly InventoryEntry[];
}): ExperimentRecord | undefined {
  if (params.params.experimentId) {
    return params.experiments.find((experiment) => experiment.id === params.params.experimentId);
  }
  if (params.params.title) {
    const requestedTitle = params.params.title.trim().toLowerCase();
    return params.experiments.find(
      (experiment) => experiment.title.toLowerCase() === requestedTitle,
    );
  }
  const plan = planScienceLabExperiments({
    experiments: params.experiments,
    progress: params.progress,
    inventory: params.inventory,
    count: 1,
  });
  return plan.selected[0]?.experiment;
}
