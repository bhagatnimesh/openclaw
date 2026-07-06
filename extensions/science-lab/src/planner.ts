import { canonicalizeMaterial, classifyMaterialsForExperiments } from "./materials.js";
import type {
  ExperimentProgress,
  ExperimentRecord,
  InventoryEntry,
  PlannedExperiment,
  ScienceLabProfile,
} from "./types.js";

export type PlanScienceLabExperimentsParams = {
  experiments: readonly ExperimentRecord[];
  progress: readonly ExperimentProgress[];
  inventory: readonly InventoryEntry[];
  profile?: ScienceLabProfile;
  count?: number;
  includeIds?: readonly string[];
  excludeIds?: readonly string[];
  replaceIds?: readonly string[];
};

export function planScienceLabExperiments(params: PlanScienceLabExperimentsParams): {
  selected: PlannedExperiment[];
  skippedExperimentIds: string[];
  materialPlan: ReturnType<typeof classifyMaterialsForExperiments>;
} {
  const count = Math.max(1, Math.min(params.count ?? 4, 50));
  const byId = new Map(params.experiments.map((experiment) => [experiment.id, experiment]));
  const progressById = new Map(params.progress.map((record) => [record.experimentId, record]));
  const includeIds = new Set(params.includeIds ?? []);
  const excludeIds = new Set([...(params.excludeIds ?? []), ...(params.replaceIds ?? [])]);
  const preferenceTags = collectPreferenceTags(params.progress);

  const selectedIds = new Set<string>();
  const selected: PlannedExperiment[] = [];
  const skippedExperimentIds: string[] = [];

  for (const includeId of includeIds) {
    if (selected.length >= count) {
      break;
    }
    const experiment = byId.get(includeId);
    if (!experiment || selectedIds.has(experiment.id)) {
      continue;
    }
    const planned = scoreExperiment({
      experiment,
      progress: progressById.get(experiment.id),
      inventory: params.inventory,
      profile: params.profile,
      preferenceTags,
      explicitlyIncluded: true,
    });
    selected.push(planned);
    selectedIds.add(experiment.id);
  }

  const candidates = params.experiments
    .filter((experiment) => {
      if (selectedIds.has(experiment.id)) {
        return false;
      }
      if (excludeIds.has(experiment.id) && !includeIds.has(experiment.id)) {
        skippedExperimentIds.push(experiment.id);
        return false;
      }
      const progress = progressById.get(experiment.id);
      if (
        !includeIds.has(experiment.id) &&
        (progress?.status === "completed" || progress?.status === "skipped")
      ) {
        skippedExperimentIds.push(experiment.id);
        return false;
      }
      return true;
    })
    .map((experiment) =>
      scoreExperiment({
        experiment,
        progress: progressById.get(experiment.id),
        inventory: params.inventory,
        profile: params.profile,
        preferenceTags,
        explicitlyIncluded: false,
      }),
    )
    .sort(comparePlannedExperiments);

  for (const candidate of candidates) {
    if (selected.length >= count) {
      break;
    }
    selected.push(candidate);
    selectedIds.add(candidate.experiment.id);
  }

  const materialPlan = classifyMaterialsForExperiments({
    experiments: selected.map((entry) => entry.experiment),
    inventory: params.inventory,
  });

  return {
    selected,
    skippedExperimentIds: [...new Set(skippedExperimentIds)].sort(),
    materialPlan,
  };
}

function scoreExperiment(params: {
  experiment: ExperimentRecord;
  progress: ExperimentProgress | undefined;
  inventory: readonly InventoryEntry[];
  profile: ScienceLabProfile | undefined;
  preferenceTags: ReadonlySet<string>;
  explicitlyIncluded: boolean;
}): PlannedExperiment {
  let score = params.explicitlyIncluded ? 1_000 : 0;
  const reasons: string[] = [];

  const ageFit = scoreAgeFit(params.experiment, params.profile);
  score += ageFit.score;
  reasons.push(ageFit.reason);

  const visualScore =
    params.experiment.visualExcitement === "high"
      ? 3
      : params.experiment.visualExcitement === "medium"
        ? 2
        : 0;
  score += visualScore;
  if (visualScore > 0) {
    reasons.push("strong visual payoff");
  }

  const waitingScore =
    params.experiment.waitingTime === "low"
      ? 3
      : params.experiment.waitingTime === "medium"
        ? 1
        : -1;
  score += waitingScore;
  if (waitingScore > 0) {
    reasons.push("low waiting time");
  }

  const reuseScore = scoreInventoryReuse(params.experiment, params.inventory);
  score += reuseScore;
  if (reuseScore > 0) {
    reasons.push("uses home inventory");
  }

  const feedbackScore = scoreFeedbackMatch(params.experiment, params.preferenceTags);
  score += feedbackScore;
  if (feedbackScore > 0) {
    reasons.push("matches child feedback");
  }

  if (params.progress?.status === "planned") {
    score -= 0.5;
    reasons.push("already planned");
  }
  if (params.explicitlyIncluded) {
    reasons.push("explicitly included");
  }

  return {
    experiment: params.experiment,
    score,
    reasons,
  };
}

function scoreAgeFit(
  experiment: ExperimentRecord,
  profile: ScienceLabProfile | undefined,
): { score: number; reason: string } {
  const target = profile?.targetAgeRange ?? { min: 4, max: 7 };
  if (!experiment.ageRange) {
    return { score: 1, reason: "age range flexible" };
  }
  const overlaps = experiment.ageRange.min <= target.max && experiment.ageRange.max >= target.min;
  if (overlaps) {
    return { score: 2, reason: "fits target age band" };
  }
  const nearby =
    Math.abs(experiment.ageRange.min - target.max) <= 1 ||
    Math.abs(target.min - experiment.ageRange.max) <= 1;
  if (nearby) {
    return { score: 0.5, reason: "near target age band" };
  }
  return { score: -2, reason: "outside target age band" };
}

function scoreInventoryReuse(
  experiment: ExperimentRecord,
  inventory: readonly InventoryEntry[],
): number {
  const haveIds = new Set(
    inventory.filter((entry) => entry.status === "have").map((entry) => entry.materialId),
  );
  let score = 0;
  for (const material of experiment.materials) {
    const { materialId } = canonicalizeMaterial(material.name);
    if (haveIds.has(materialId)) {
      score += 0.5;
    }
  }
  return Math.min(score, 3);
}

function collectPreferenceTags(progress: readonly ExperimentProgress[]): ReadonlySet<string> {
  const tags = new Set<string>();
  for (const record of progress) {
    for (const tag of record.preferenceTags) {
      const normalized = tag.toLowerCase().trim();
      if (normalized) {
        tags.add(normalized);
      }
    }
  }
  return tags;
}

function scoreFeedbackMatch(
  experiment: ExperimentRecord,
  preferenceTags: ReadonlySet<string>,
): number {
  if (preferenceTags.size === 0) {
    return 0;
  }
  const haystack = [
    experiment.title,
    experiment.category,
    experiment.difficulty,
    ...experiment.concepts,
    ...experiment.materials.map((material) => material.name),
  ]
    .filter(Boolean)
    .join(" ")
    .toLowerCase();
  let score = 0;
  for (const tag of preferenceTags) {
    if (haystack.includes(tag)) {
      score += 1;
    }
  }
  return Math.min(score, 3);
}

function comparePlannedExperiments(a: PlannedExperiment, b: PlannedExperiment): number {
  if (b.score !== a.score) {
    return b.score - a.score;
  }
  const orderA = a.experiment.libraryOrder ?? Number.MAX_SAFE_INTEGER;
  const orderB = b.experiment.libraryOrder ?? Number.MAX_SAFE_INTEGER;
  if (orderA !== orderB) {
    return orderA - orderB;
  }
  return (
    a.experiment.title.localeCompare(b.experiment.title) ||
    a.experiment.id.localeCompare(b.experiment.id)
  );
}
