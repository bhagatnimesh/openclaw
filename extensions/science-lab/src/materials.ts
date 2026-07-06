import {
  MATERIAL_PLAN_CATEGORY_LABELS,
  type ExperimentRecord,
  type InventoryEntry,
  type MaterialPlan,
  type MaterialPlanCategory,
  type MaterialPlanItem,
  type MaterialRule,
} from "./types.js";

const DEFAULT_RULES: readonly MaterialRule[] = [
  {
    materialId: "water",
    displayName: "Water",
    category: "likely_already_at_home",
    aliases: ["tap water", "warm water", "cold water"],
  },
  {
    materialId: "salt",
    displayName: "Salt",
    category: "likely_already_at_home",
    aliases: ["table salt"],
  },
  {
    materialId: "sugar",
    displayName: "Sugar",
    category: "likely_already_at_home",
    aliases: ["white sugar"],
  },
  {
    materialId: "milk",
    displayName: "Milk",
    category: "please_confirm",
    aliases: ["whole milk"],
  },
  {
    materialId: "ice",
    displayName: "Ice",
    category: "likely_already_at_home",
    aliases: ["ice cubes"],
  },
  {
    materialId: "spoon",
    displayName: "Spoon",
    category: "likely_already_at_home",
    aliases: ["metal spoon", "plastic spoon"],
  },
  {
    materialId: "bowl",
    displayName: "Bowl",
    category: "likely_already_at_home",
    aliases: ["mixing bowl"],
  },
  {
    materialId: "paper-towels",
    displayName: "Paper towels",
    category: "likely_already_at_home",
    aliases: ["paper towel"],
  },
  {
    materialId: "measuring-cups",
    displayName: "Measuring cups",
    category: "likely_already_at_home",
    aliases: ["measuring cup"],
  },
  {
    materialId: "measuring-spoons",
    displayName: "Measuring spoons",
    category: "likely_already_at_home",
    aliases: ["measuring spoon"],
  },
  {
    materialId: "zip-top-bags",
    displayName: "Zip-top bags",
    category: "please_confirm",
    aliases: ["zip bag", "zip bags", "ziploc bag", "ziploc bags", "sandwich bags"],
  },
  {
    materialId: "clear-cups",
    displayName: "Clear cups",
    category: "please_confirm",
    aliases: ["clear plastic cups", "clear cup", "plastic cups"],
  },
  {
    materialId: "vegetable-oil",
    displayName: "Vegetable oil",
    category: "please_confirm",
    aliases: ["cooking oil", "oil"],
  },
  {
    materialId: "food-coloring",
    displayName: "Food coloring",
    category: "please_confirm",
    aliases: ["food dye"],
  },
  {
    materialId: "heavy-cream",
    displayName: "Heavy cream",
    category: "please_confirm",
    aliases: ["cream", "whipping cream"],
  },
  {
    materialId: "rock-salt",
    displayName: "Rock salt",
    category: "recommended_amazon_order",
    amazonQuery: "rock salt for ice cream making",
    aliases: ["ice cream salt"],
  },
  {
    materialId: "safety-goggles",
    displayName: "Safety goggles",
    category: "recommended_amazon_order",
    amazonQuery: "child safety goggles science",
    aliases: ["safety glasses", "kids safety goggles"],
  },
  {
    materialId: "pipettes",
    displayName: "Pipettes",
    category: "recommended_amazon_order",
    amazonQuery: "plastic pipettes for kids science",
    aliases: ["dropper", "droppers", "plastic droppers"],
  },
  {
    materialId: "effervescent-antacid-tablets",
    displayName: "Effervescent antacid tablets",
    category: "recommended_amazon_order",
    amazonQuery: "effervescent antacid tablets",
    aliases: ["alka seltzer", "antacid tablet", "antacid tablets"],
  },
  {
    materialId: "ph-strips",
    displayName: "pH strips",
    category: "recommended_amazon_order",
    amazonQuery: "pH test strips",
    aliases: ["ph paper", "litmus paper"],
  },
  {
    materialId: "magnets",
    displayName: "Magnets",
    category: "recommended_amazon_order",
    amazonQuery: "science magnets for kids",
    aliases: ["magnet"],
  },
];

const RULES_BY_ID = new Map(DEFAULT_RULES.map((rule) => [rule.materialId, rule]));
const RULES_BY_NORMALIZED_NAME = new Map<string, MaterialRule>();

for (const rule of DEFAULT_RULES) {
  RULES_BY_NORMALIZED_NAME.set(normalizeMaterialName(rule.displayName), rule);
  for (const alias of rule.aliases) {
    RULES_BY_NORMALIZED_NAME.set(normalizeMaterialName(alias), rule);
  }
}

export function normalizeMaterialName(value: string): string {
  return value
    .toLowerCase()
    .replace(/&/gu, " and ")
    .replace(/[^a-z0-9]+/gu, " ")
    .trim()
    .replace(/\s+/gu, " ");
}

export function normalizeMaterialId(value: string): string {
  const normalized = normalizeMaterialName(value);
  return normalized.replace(/\s+/gu, "-") || "material";
}

export function canonicalizeMaterial(input: string): {
  materialId: string;
  displayName: string;
  rule?: MaterialRule;
} {
  const normalized = normalizeMaterialName(input);
  const rule = RULES_BY_NORMALIZED_NAME.get(normalized);
  if (rule) {
    return {
      materialId: rule.materialId,
      displayName: rule.displayName,
      rule,
    };
  }

  const materialId = normalizeMaterialId(input);
  const knownRule = RULES_BY_ID.get(materialId);
  if (knownRule) {
    return {
      materialId: knownRule.materialId,
      displayName: knownRule.displayName,
      rule: knownRule,
    };
  }

  const displayName = input.trim().replace(/\s+/gu, " ");
  return {
    materialId,
    displayName: displayName || "Material",
  };
}

export function amazonSearchUrl(query: string): string {
  const encoded = encodeURIComponent(query.trim()).replace(/%20/gu, "+");
  return `https://www.amazon.com/s?k=${encoded}`;
}

export function emptyMaterialPlan(): MaterialPlan {
  return {
    alreadyInHomeInventory: [],
    likelyAlreadyAtHome: [],
    pleaseConfirm: [],
    recommendedAmazonOrder: [],
  };
}

export function classifyMaterialsForExperiments(params: {
  experiments: readonly ExperimentRecord[];
  inventory: readonly InventoryEntry[];
}): MaterialPlan {
  const inventoryByMaterialId = new Map(params.inventory.map((entry) => [entry.materialId, entry]));
  const aggregated = new Map<
    string,
    {
      displayName: string;
      rule?: MaterialRule;
      experimentIds: Set<string>;
      experimentTitles: Set<string>;
      notes: Set<string>;
    }
  >();

  for (const experiment of params.experiments) {
    for (const material of experiment.materials) {
      const canonical = canonicalizeMaterial(material.name);
      const existing = aggregated.get(canonical.materialId) ?? {
        displayName: canonical.displayName,
        ...(canonical.rule ? { rule: canonical.rule } : {}),
        experimentIds: new Set<string>(),
        experimentTitles: new Set<string>(),
        notes: new Set<string>(),
      };
      existing.experimentIds.add(experiment.id);
      existing.experimentTitles.add(experiment.title);
      if (material.quantity) {
        existing.notes.add(material.quantity);
      }
      if (material.notes) {
        existing.notes.add(material.notes);
      }
      aggregated.set(canonical.materialId, existing);
    }
  }

  const plan = emptyMaterialPlan();
  for (const [materialId, material] of aggregated) {
    const inventoryEntry = inventoryByMaterialId.get(materialId);
    const category = resolveMaterialPlanCategory(material.rule, inventoryEntry);
    const amazonQuery = material.rule?.amazonQuery ?? material.displayName;
    const item: MaterialPlanItem = {
      materialId,
      displayName: inventoryEntry?.displayName ?? material.displayName,
      category,
      categoryLabel: MATERIAL_PLAN_CATEGORY_LABELS[category],
      usedByExperimentIds: [...material.experimentIds].sort(),
      usedByExperimentTitles: [...material.experimentTitles].sort(),
      ...(inventoryEntry ? { inventoryStatus: inventoryEntry.status } : {}),
      ...(inventoryEntry?.quantity ? { quantity: inventoryEntry.quantity } : {}),
      ...(material.notes.size > 0 || inventoryEntry?.notes
        ? { notes: [...material.notes, inventoryEntry?.notes].filter(Boolean).join("; ") }
        : {}),
      ...(category === "recommendedAmazonOrder"
        ? {
            amazonSearchLink: amazonSearchUrl(amazonQuery),
            amazonSearchLabel: "Amazon search link" as const,
          }
        : {}),
    };
    plan[category].push(item);
  }

  for (const category of Object.keys(plan) as MaterialPlanCategory[]) {
    plan[category].sort((a, b) => a.displayName.localeCompare(b.displayName));
  }

  return plan;
}

function resolveMaterialPlanCategory(
  rule: MaterialRule | undefined,
  inventoryEntry: InventoryEntry | undefined,
): MaterialPlanCategory {
  if (inventoryEntry?.status === "have") {
    return "alreadyInHomeInventory";
  }
  if (inventoryEntry?.status === "missing" || inventoryEntry?.status === "low") {
    return "recommendedAmazonOrder";
  }
  if (inventoryEntry?.status === "unknown") {
    return "pleaseConfirm";
  }

  if (rule?.category === "recommended_amazon_order") {
    return "recommendedAmazonOrder";
  }
  if (rule?.category === "likely_already_at_home") {
    return "likelyAlreadyAtHome";
  }
  return "pleaseConfirm";
}
