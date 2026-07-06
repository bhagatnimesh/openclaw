# Family Decisions User Guide

Family Decisions helps the family keep track of open choices that need discussion, research, ownership, and follow-through. Use it for questions that should not disappear into chat history: camp plans, birthday parties, school choices, travel tradeoffs, medical logistics, and other family decisions that may take more than one conversation.

The workflow is simple:

1. Capture the decision.
2. Add options, evidence, notes, owners, and next steps as they emerge.
3. Ask for a decision brief before discussing.
4. Record the final decision.
5. Review open decisions regularly.

## Capture A Decision

Start with the phrase `Captured decision:` or `Track decision about`. Keep the first sentence as the decision title. Add optional details naturally.

```text
Captured decision: Summer camp plan for Nysha for the last week.
Options are stay at home, go to ICC, challenges she will be jetlagged
```

Expected behavior:

- Creates one open decision.
- Uses `Summer camp plan for Nysha for the last week` as the title.
- Adds `Stay at home` and `Go to ICC` as options.
- Adds `She will be jetlagged` as evidence.
- Reports missing fields such as owner, timeline, options, or next step.

Other useful capture examples:

```text
Track decision about school choice owner both by next Monday
```

```text
Are we going to Rahul's birthday party?
```

```text
Decide school option. I want Noah to compare commute, waitlist, and fit.
```

## Add More Detail Later

After a decision is open, short follow-up messages attach to the latest open decision. You do not need to remember the short decision id for normal follow-ups.

### Add options

```text
options are stay at home, go to ICC
```

```text
add option: ask another family if they want to do a playdate instead
```

### Add notes, evidence, or concerns

```text
Added note to call FUSD to get Nysha waiting list number
```

```text
challenges she will be jetlagged
```

```text
concerns are commute time and after-school fatigue
```

### Add next steps

```text
add next step: call FUSD owner dad by tomorrow
```

```text
next step: ask ICC if late pickup is available owner mom
```

The feature understands the built-in owners `dad`, `mom`, `both`, `family`, and `unknown`. If owner or timeline is missing, the decision brief will call that out.

## Ask For A Decision Brief

Ask for a brief when you are ready to discuss the decision. If you do not include an id, Family Decisions uses the latest open decision.

```text
provide decision brief
```

```text
give me decision brief
```

```text
give me decision bried
```

The brief includes:

- status, owner, and due date
- context
- options
- evidence
- open next steps
- AI assist prompts for missing information
- the outcome, once decided

Use the brief as the pre-discussion snapshot. If it says options, timeline, owner, or next step are missing, add those before trying to decide.

## Record The Decision

When the family has decided, say what was chosen.

```text
we decided: choose ICC for the last week
```

```text
decision is stay home and schedule two playdates
```

This marks the latest open decision as decided when no id is included.

You can also close a decision by the number shown in the pending decisions list:

```text
close decision 2 done
```

```text
close the decision 2. Give me decision bried done
```

## Review Open Decisions

Use a review prompt when you want to see what still needs attention.

```text
list open decisions
```

```text
show pending decisions
```

```text
tell me the pending decisions
```

Open decisions are ordered so urgent, due, and recently updated items surface first.

## When To Use A Decision Instead Of A Task Or Event

Use a decision when the family has not chosen yet.

| Message shape | Use |
| --- | --- |
| `Call FUSD Monday at 2 PM` | Calendar event or task |
| `Need to decide summer camp plan` | Decision |
| `Options are stay home, ICC` | Decision update |
| `Add task call FUSD` | Task |
| `Are we going to the birthday party?` | Decision |

A good rule: if the question is "what should we choose?", capture a decision. If the question is "what should someone do?", create a task. If the question is "when is it happening?", create a calendar event.

## Good Decision Hygiene

For important decisions, try to fill these fields before deciding:

- **Owner:** who is responsible for moving the decision forward.
- **Timeline:** when the decision needs to be made.
- **Options:** what choices are on the table.
- **Evidence:** facts, constraints, concerns, or research.
- **Next step:** one concrete action that moves the decision forward.

Examples:

```text
add next step: ask Chadbourne for waitlist status owner dad by tomorrow
```

```text
add note: Nysha wants to be with a familiar friend if possible
```

```text
options are stay at home, ICC, ask another family about shared childcare
```

## How AI Helps In V1

In v1, AI support is focused on structure and reminders:

- catches common voice and typo mistakes such as `decision bried`
- separates voice-note text into title, options, and evidence
- routes follow-up notes to the latest open decision
- identifies missing owner, timeline, options, or next step
- produces a decision brief for family discussion

Research support is captured as part of the decision, but live autonomous research is not fully wired yet. For now, ask for research support explicitly and store the request as a next step or evidence note:

```text
add next step: Noah should compare ICC hours, cost, and commute owner dad by tomorrow
```

```text
add note: need research on jetlag impact after travel
```

## Recovery Tips

If the system asks which feature to use, reply:

```text
decisions
```

If the wrong decision receives a follow-up, ask for the open decisions list and then include the short id from the list:

```text
list open decisions
```

```text
add note 05d837f2: ICC pickup may be too late
```

If a typo gets through, restate the message with an explicit decision word:

```text
give me decision brief
```

```text
add note to latest decision: call FUSD for the waiting list number
```
