---
title: Science Lab Plugin
sidebarTitle: Science Lab
summary: Plan private home science experiments with live inventory, material search links, guides, and reflection.
read_when:
  - You want an OpenClaw science planning helper for home experiments.
  - You need inventory-aware material planning and parent guide output.
  - You want to understand the privacy and copyright boundaries.
---

# Science Lab Plugin

The Science Lab plugin helps a parent plan home experiments, track live materials, prepare a kid-friendly parent guide, and record what children noticed afterward. It ships with the `science-lab` skill, which defines the warm coach parent voice, everyday magic kid script voice, curious reset style, and guide checklist.

The plugin is intentionally private-first. Uploaded pages, images, OCR, or pasted experiment notes are treated as local source input. OpenClaw stores structured experiment facts and live planning state in plugin state. It does not ship book photos, copied instructions, or source OCR in the repository.

## Setup

Enable the plugin and allow either the plugin id or the specific tools:

```json
{
  "plugins": {
    "entries": {
      "science-lab": {
        "enabled": true
      }
    }
  },
  "tools": {
    "allow": ["science-lab"]
  }
}
```

For a tighter setup, allow individual tools such as `science_lab_plan` and `science_lab_guide_context`.

## Tools

- `science_lab_profile`: set or list child names, ages, interests, and the target age band.
- `science_lab_import_experiments`: upsert structured experiment records extracted from private user-provided images or text.
- `science_lab_inventory`: list or update material status as `have`, `missing`, `low`, or `unknown`.
- `science_lab_plan`: select the next experiments, defaulting to four, with include, exclude, and replace controls.
- `science_lab_experiment_status`: mark an experiment planned, completed, or skipped and record feedback or questions.
- `science_lab_guide_context`: return experiment facts, material plan, guide checklist, video search queries, real-world prompts, and an image prompt seed.
- `science_lab_save_guide`: save final Markdown and image prompt Markdown under `outputs/science_lab_guides/`.

## State And Artifacts

Live app state uses SQLite-backed plugin state through `api.runtime.state.openKeyedStore`. The plugin stores profile, experiment records, inventory, progress, and saved guide metadata in plugin-state namespaces.

Markdown guides are named user artifacts. By default they are saved under:

```text
outputs/science_lab_guides/
```

The save tool rejects absolute paths and parent-relative paths so guide output stays inside the workspace.

## Material Planning

Material categories are:

- Already in Home Inventory
- Likely Already at Home
- Please Confirm
- Recommended Amazon Order

Amazon links are generated as search URLs, for example:

```text
https://www.amazon.com/s?k=child+safety+goggles+science
```

They are labeled "Amazon search link." They are not product recommendations, affiliate links, price claims, availability claims, or compliance with Amazon product APIs.

## Example Workflows

Import private experiment notes:

```text
Extract structured experiment records from these uploaded pages, keep source text private, then import them into Science Lab.
```

Plan the next four:

```text
Use Science Lab to plan the next 4 experiments. We already have salt, food coloring, clear cups, and zip bags. Replace anything that needs borax.
```

Generate a guide:

```text
Create the parent guide for Ice Cream in a Bag using the Science Lab personality. Include the image prompt and save the guide.
```

Record reflection:

```text
Mark Ice Cream in a Bag complete. The kids loved shaking and asked why salt made ice colder. Tag this with ice, food, and motion.
```

## Guide Voice

Science Lab guides should feel like a prepared parent coaching a real kitchen-table experiment. They should ask for predictions before explanations, celebrate guesses, use child words first, and end with wonder plus a next question.

Safety stays adult-facing and direct. Hazards, allergens, heat, glass, small parts, food safety, and cleanup should be clear without sounding scary.
