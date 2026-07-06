import { canonicalizeMaterial, normalizeMaterialId, normalizeMaterialName } from "./materials.js";
import type { InventoryEntry, MaterialStatus } from "./types.js";

export type InventoryUpdateInput = {
  name: string;
  status: MaterialStatus;
  quantity?: string;
  notes?: string;
};

const STATUS_PATTERNS: readonly {
  status: MaterialStatus;
  patterns: readonly RegExp[];
}[] = [
  {
    status: "missing",
    patterns: [
      /\b(?:we\s+)?(?:do\s+not|dont|don't)\s+have\s+(.+)$/iu,
      /\b(?:we\s+are\s+)?out\s+of\s+(.+)$/iu,
      /\bmissing\s+(.+)$/iu,
      /\bneed\s+to\s+buy\s+(.+)$/iu,
      /\bno\s+(.+)$/iu,
    ],
  },
  {
    status: "low",
    patterns: [/\blow\s+on\s+(.+)$/iu, /\balmost\s+out\s+of\s+(.+)$/iu],
  },
  {
    status: "unknown",
    patterns: [/\bnot\s+sure\s+about\s+(.+)$/iu, /\bunknown\s+(.+)$/iu],
  },
  {
    status: "have",
    patterns: [
      /\b(?:we\s+)?have\s+(.+)$/iu,
      /\b(?:we\s+)?bought\s+(.+)$/iu,
      /\bavailable\s+(.+)$/iu,
    ],
  },
];

export function parseInventoryText(text: string): InventoryUpdateInput[] {
  const updates: InventoryUpdateInput[] = [];
  const segments = text
    .split(/[.;\n]+/u)
    .map((segment) => segment.trim())
    .filter(Boolean);

  for (const segment of segments) {
    const parsed = parseInventorySegment(segment);
    updates.push(...parsed);
  }

  return dedupeInventoryUpdates(updates);
}

export function createInventoryEntry(update: InventoryUpdateInput, now: string): InventoryEntry {
  const canonical = canonicalizeMaterial(update.name);
  return {
    version: 1,
    materialId: canonical.materialId || normalizeMaterialId(update.name),
    displayName: canonical.displayName,
    status: update.status,
    ...(update.quantity?.trim() ? { quantity: update.quantity.trim() } : {}),
    ...(update.notes?.trim() ? { notes: update.notes.trim() } : {}),
    lastUpdated: now,
  };
}

function parseInventorySegment(segment: string): InventoryUpdateInput[] {
  const normalized = segment.trim().replace(/\s+/gu, " ");
  for (const group of STATUS_PATTERNS) {
    for (const pattern of group.patterns) {
      const match = normalized.match(pattern);
      if (!match?.[1]) {
        continue;
      }
      return splitMaterialList(match[1]).map((name) => ({
        name,
        status: group.status,
      }));
    }
  }
  return [];
}

function splitMaterialList(value: string): string[] {
  return value
    .replace(/\band\b/giu, ",")
    .split(",")
    .map(cleanMaterialPhrase)
    .filter(Boolean);
}

function cleanMaterialPhrase(value: string): string {
  return value
    .trim()
    .replace(/^(?:some|a|an|the)\s+/iu, "")
    .replace(/\s+already$/iu, "")
    .replace(/\s+/gu, " ");
}

function dedupeInventoryUpdates(updates: InventoryUpdateInput[]): InventoryUpdateInput[] {
  const byId = new Map<string, InventoryUpdateInput>();
  for (const update of updates) {
    byId.set(normalizeMaterialName(update.name), update);
  }
  return [...byId.values()];
}
