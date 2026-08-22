---
tags:
  - "n4os/core"
  - "n4os/import"
links:
  - "[[README]]"
  - "[[MISSION]]"
  - "[[PRIORITIES]]"
  - "[[family/Nysha|Nysha]]"
  - "[[family/Navya|Navya]]"
---

# N4OS Importer

Use this instruction when a file, link, image text, PDF, slide deck, document, email, school packet, meeting note, or rough note should be added to N4OS as second-brain material.

The importer is not a summarizer. It converts source material into durable Markdown that can be found, trusted, reused, and woven into coaching, planning, preparation, reminders, prompts, and future decisions.

## Input

An import request should include:

- Source: file, link, pasted text, image OCR, or document export.
- Intent: what the material is for.
- Scope: who or what it belongs to.
- Desired future uses: examples such as lookup, planning, review, reference, reminders, task extraction, decision support, prep material, relationship-building, conversation starters, or domain-specific coaching.

Example:

```text
/import second brain <link>
Instructions: This is Nysha's 2nd grade class guide. Store whatever matters for the family second brain: facts, routines, expectations, people, resources, decisions, prep material, conversation starters, follow-up actions, and future questions this should help answer.
```

## Import Rule

Always preserve the source before turning it into operating material.

After the user approves the import plan, create or update Markdown in three layers:

1. Source note: factual extract, citation, date, provenance, and raw useful details.
2. Routed notes: topic-specific Markdown files where the knowledge will naturally be looked up later.
3. Action layer: prompts, checklists, routines, task candidates, conversation starters, or review questions.

Do not bury everything in one long summary. Split by future use.

## Routing

Choose the smallest set of destination files that makes the material useful later.

Use existing files when the material updates a stable area:

- Family profile: `family/<Name>.md`
- Family observations: `family/observations/YYYY-MM.md`
- Homework or school routines: `homework/<Name>/`
- Reading: `Reading.md` or child-specific homework/reading files
- Parenting approach: `playbooks/Parenting.md`
- Daily/weekly review: `daily/` or `reviews/`
- Decision material: `FAMILY_DECISIONS_GUIDE.md` patterns, or captured decision state if available
- General learning: `learnings/`
- Raw inbox material that is not sorted yet: `learnings/Inbox.md`

Create new folders or files only after showing the plan and getting approval. New folders are appropriate when the source introduces a durable domain that will recur.

Recommended durable domain examples:

```text
school/<Child>/<School Year>/
health/<Person>/
finance/
home/
travel/
projects/
```

## Output Files

For a rich source, prefer this pattern:

```text
<domain>/<subject>/Source - <Source Name>.md
<domain>/<subject>/Overview.md
<domain>/<subject>/<Reusable Lens>.md
<domain>/<subject>/<Action Lens>.md
```

For a school class guide, use:

```text
school/Nysha/2026-2027/Source - Back to School Night.md
school/Nysha/2026-2027/School Knowledge.md
school/Nysha/2026-2027/Room 13.md
school/Nysha/2026-2027/Curriculum Map.md
school/Nysha/2026-2027/Homework System.md
school/Nysha/2026-2027/Teacher Approach.md
school/Nysha/2026-2027/Parent Support Playbook.md
school/Nysha/2026-2027/Conversation Starters.md
school/Nysha/2026-2027/Resources.md
```

### Recurring School Newsletters

Newsletter imports update the existing school-year knowledge pack instead of creating child-profile facts. Route current topics and routines to `School Knowledge.md`, detailed skills to `Curriculum Map.md`, books/media/platforms to `Resources.md`, and source-backed prompts to `Conversation Starters.md`. Keep a compact dated audit record in `family/observations/YYYY-MM.md`, but do not label class-wide material as a personal `Observation`.

Explicit assignments, dates, and required reminders may use the normal homework, calendar, and task flows after preview approval. Recommendations such as optional weekly practice are saved as context and shown as task candidates; they are never scheduled automatically.

## Markdown Shape

Each imported file should include frontmatter:

```yaml
---
tags:
  - "n4os/imported"
  - "domain/topic"
source:
  type: "slides|pdf|doc|email|note|image|web"
  title: "<source title>"
  url: "<source url if any>"
  imported: "YYYY-MM-DD"
  confidence: "high|medium|low"
links:
  - "[[relevant note]]"
---
```

Use these sections when relevant:

```text
# Title

## What This Is
## Key Facts
## Operating Meaning
## How N4OS Should Use This
## Questions This Can Answer
## Action Ideas
## Open Questions
## Source Notes
```

## Extraction Rules

- Keep factual claims tied to the source.
- Separate source facts from N4OS interpretation.
- Preserve names, dates, times, URLs, class codes, contact channels, routines, and expectations.
- Extract recurring systems, not just events.
- Convert instructions into checklists only when they imply repeated parent action.
- Convert dates into calendar/task candidates only when the source asks for a real action or event.
- Do not promote one-off or uncertain observations into a child profile.
- Put hypotheses or child-specific patterns in monthly observations first.
- Use links generously so Obsidian and future retrieval can connect related ideas.

## Future-Use Index

Every import should answer: "What can I ask N4OS later because this exists?"

Add a broad section like:

```text
## Questions This Can Answer

- What is Nysha learning at school?
- How should I help her with homework this week?
- Design a 20-minute practice class aligned with her teacher's approach.
- Give me low-pressure conversation starters for the car.
- What should I expect from school routines on Friday?
- What parent support materials should I use?
- What follow-up tasks, calendar items, or questions should we track?
- What durable family context should this update?
- Which future decisions or reviews should use this source?
```

## Action Layer

When useful, create reusable action material:

- Conversation starters
- Prep checklist
- Home practice plan
- Weekly review prompts
- Questions for teacher
- Calendar candidates
- Task candidates
- Resource list

Keep action material separate from raw source facts so N4OS can use it directly.

## Import Plan

Before writing files, always show a plan. The plan is the approval boundary for creating new files.

The plan should include:

- Source being imported.
- Files to create.
- Existing files to update.
- Why each file belongs in N4OS.
- Future use cases the import will enable.
- Any uncertain interpretation that needs user correction.

Do not create new files until the user approves the plan. If the user approves only part of the plan, create or update only that part.

## Import Preview

The plan should use this preview format:

```text
N4OS import preview: <title>

Source:
- <source title and type>

Proposed new files:
- <file>: <purpose>

Proposed updates:
- <file>: <purpose>

Future uses enabled:
- Answer future family questions with source-backed context.
- Turn important material into plans, prep checklists, routines, prompts, tasks, and review questions.
- Connect this source to the right N4OS people, domains, decisions, goals, and playbooks.
- Include domain-specific uses from the source, without treating the user's examples as a limit.

Needs confirmation:
- <uncertain item>

Reply `save` to approve this plan, `adjust` with changes, or `cancel`.
```

## Save Behavior

When saving after plan approval:

- Write the source note first.
- Then write routed notes.
- Then add backlinks from existing stable files only when useful.
- Add tasks or calendar items only when the user explicitly confirms them.
- Never silently overwrite human-written nuance. Append or update clearly marked imported sections.

Use markers for generated import blocks:

```text
<!-- n4os-import:<stable-source-id> -->
```

## Quality Bar

A good import makes future N4OS answers more specific.

Bad:

```text
Nysha has a school guide. It talks about reading, math, and homework.
```

Good:

```text
Nysha's Room 13 homework packet comes home Friday, is designed for Monday-Thursday, should stay in the Homework Folder, and is expected back Friday at 8:30 a.m. Parent review/signature is part of the routine.
```

The goal is not to store more text. The goal is to make the family operating system smarter.
