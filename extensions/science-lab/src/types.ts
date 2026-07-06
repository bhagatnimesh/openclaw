import type {
  OpenKeyedStoreOptions,
  PluginStateKeyedStore,
} from "openclaw/plugin-sdk/plugin-state-runtime";

export const SCIENCE_LAB_PLUGIN_ID = "science-lab";
export const DEFAULT_GUIDE_OUTPUT_DIR = "outputs/science_lab_guides";

export type OpenScienceLabStateStore = <T>(
  options: OpenKeyedStoreOptions,
) => PluginStateKeyedStore<T>;

export type MaterialStatus = "have" | "missing" | "low" | "unknown";
export type ExperimentStatus = "planned" | "completed" | "skipped";
export type TimeBand = "low" | "medium" | "high";
export type MaterialRuleCategory =
  | "likely_already_at_home"
  | "please_confirm"
  | "recommended_amazon_order";
export type MaterialPlanCategory =
  | "alreadyInHomeInventory"
  | "likelyAlreadyAtHome"
  | "pleaseConfirm"
  | "recommendedAmazonOrder";

export const MATERIAL_PLAN_CATEGORY_LABELS = {
  alreadyInHomeInventory: "Already in Home Inventory",
  likelyAlreadyAtHome: "Likely Already at Home",
  pleaseConfirm: "Please Confirm",
  recommendedAmazonOrder: "Recommended Amazon Order",
} satisfies Record<MaterialPlanCategory, string>;

export type ChildProfile = {
  name: string;
  age?: number;
  interests: string[];
};

export type ScienceLabProfile = {
  version: 1;
  children: ChildProfile[];
  targetAgeRange: {
    min: number;
    max: number;
  };
  updatedAt: string;
};

export type ExperimentMaterial = {
  name: string;
  quantity?: string;
  notes?: string;
};

export type ExperimentRecord = {
  version: 1;
  id: string;
  title: string;
  sourceLabel?: string;
  sourceRef?: string;
  category?: string;
  difficulty?: string;
  prepMinutes?: number;
  activeMinutes?: number;
  waitingTime: TimeBand;
  messLevel?: TimeBand;
  ageRange?: {
    min: number;
    max: number;
  };
  concepts: string[];
  materials: ExperimentMaterial[];
  visualExcitement: TimeBand;
  safetyNotes: string[];
  sourceNotes?: string;
  libraryOrder?: number;
  updatedAt: string;
};

export type InventoryEntry = {
  version: 1;
  materialId: string;
  displayName: string;
  status: MaterialStatus;
  quantity?: string;
  notes?: string;
  lastUpdated: string;
};

export type MaterialRule = {
  materialId: string;
  displayName: string;
  category: MaterialRuleCategory;
  amazonQuery?: string;
  aliases: string[];
};

export type ExperimentProgress = {
  version: 1;
  experimentId: string;
  status: ExperimentStatus;
  plannedAt?: string;
  completedAt?: string;
  skippedAt?: string;
  feedback?: string;
  childFeedback: string[];
  childQuestions: string[];
  preferenceTags: string[];
  parentNotes?: string;
  updatedAt: string;
};

export type MaterialPlanItem = {
  materialId: string;
  displayName: string;
  category: MaterialPlanCategory;
  categoryLabel: string;
  usedByExperimentIds: string[];
  usedByExperimentTitles: string[];
  inventoryStatus?: MaterialStatus;
  quantity?: string;
  notes?: string;
  amazonSearchLink?: string;
  amazonSearchLabel?: "Amazon search link";
};

export type MaterialPlan = Record<MaterialPlanCategory, MaterialPlanItem[]>;

export type PlannedExperiment = {
  experiment: ExperimentRecord;
  score: number;
  reasons: string[];
};

export type GuideContext = {
  experiment: ExperimentRecord;
  materialPlan: MaterialPlan;
  guideChecklist: string[];
  personalityContract: {
    parentVoice: string;
    kidScriptVoice: string;
    failureStyle: string;
    safetyTone: string;
  };
  styleRules: string[];
  videoSearchQueries: string[];
  realWorldPrompts: string[];
  imagePromptSeed: string;
};

export type SavedGuideRecord = {
  version: 1;
  experimentId: string;
  guidePath: string;
  imagePromptPath?: string;
  savedAt: string;
};
