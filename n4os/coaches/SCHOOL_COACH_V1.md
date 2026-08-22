# N4OS School Relationship Coach — V1 Spec

## Mission

Help the parent build **warm, trusted, durable, two-way relationships** with the important adults involved in each child's education.

Potential relationship targets include:

- primary classroom teachers,
- after-school teachers,
- specialist teachers,
- coaches,
- school leadership,
- other adults who become important during the school year.

The goal is relationship quality, not communication volume.

A strong relationship should increasingly mean:

- the teacher knows the parent beyond administrative exchanges,
- the parent understands the teacher's priorities and style,
- communication feels comfortable in both directions,
- interactions are not dominated by problems,
- appreciation is specific and authentic,
- concerns can be discussed candidly,
- useful information flows both ways,
- both sides feel they are collaborating around the child.

## V1 Product Thesis

A persistent coach can:

1. maintain a model of each teacher relationship,
2. maintain an explicit relationship strategy,
3. use current N4OS context,
4. identify a small number of useful interaction opportunities,
5. prepare the parent for those interactions,
6. capture what happened,
7. learn from feedback and outcomes,
8. visibly change its future coaching.

## Relationship Model

Conceptually:

```yaml
relationship:
  id:
  person:
  role:
  child:
  school:

  current_assessment:
  desired_state:
  relationship_stage:
  confidence:

  teacher_model:
    communication_preferences:
    priorities:
    interaction_style:
    known_context:

  parent_model:
    effective_interaction_patterns:
    friction_patterns:

  recent_interactions: []
  open_loops: []
  opportunities: []
  risks: []

  strategy:
    current_focus:
    next_milestone:
    hypotheses: []

  last_meaningful_interaction:
  next_expected_interaction:
```

Sparse data is acceptable. Unknown fields should remain unknown.

## Relationship Stages

Use internally as a reasoning aid:

```text
Unknown
→ Introduced
→ Familiar
→ Comfortable
→ Trusted
→ Collaborative
```

Do not turn this into gamification.

## Day Zero Experience

Before asking the user questions, inspect existing N4OS context.

Then conduct a lightweight onboarding conversation only for genuinely missing information:

- who are the important teachers/adults,
- what relationship exists today,
- what the parent wants from the relationship,
- how communication usually happens,
- what has historically been difficult,
- how proactive the coach should be.

The first useful output should be an **initial relationship brief**, not a settings screen.

Example:

```text
Ms. X — Classroom Teacher

Current state
New relationship. You met briefly at back-to-school night.

What I know
- School started recently.
- Pickup may provide occasional in-person access.
- You want better ongoing communication than in previous years.
- There are no current concerns requiring intervention.

Goal for the next few weeks
Build familiarity without creating unnecessary communication.

Plan
1. Have one short positive interaction.
2. Learn something about how she sees the child settling in.
3. Avoid turning early conversations into repeated performance checks.
4. Learn what communication style feels natural for her.

Next opportunity
Pickup on Thursday may be useful.

Suggested question
"How does she seem to be settling into the class so far?"

Confidence
Medium — we do not yet know whether pickup is convenient for this teacher.
```

## Living Coaching Brief

At any time, support questions such as:

- "What's your current thinking about Ms. X?"
- "Show me your strategy for Ms. X."
- "Why are you suggesting this?"
- "What have you learned about this relationship?"

The response should be grounded in persisted state.

Include:

- current assessment,
- evidence,
- goal,
- strategy,
- next likely opportunity,
- risks,
- uncertainty,
- recent learning,
- open loops.

## Multi-Horizon Planning

The coach should connect:

### School-year horizon
Desired relationship state.

### Monthly / term horizon
Current relationship milestone.

### Weekly horizon
Current focus.

### Moment horizon
The next specific interaction.

Moment-level advice should be explainable by the longer-term strategy.

## Intervention Vocabulary

Do not reduce the product to teacher messages.

Support:

- prepare,
- ask,
- appreciate,
- share,
- follow up,
- listen,
- observe,
- reflect,
- draft message,
- prepare for important conversation,
- no action.

Examples:

### Ask
"At pickup, ask this one question."

### Appreciate
"Your child mentioned the science experiment repeatedly. This is a natural positive note to share."

### Observe
"Pay attention to whether the teacher seems open to pickup conversations."

### Listen
"Don't add another topic. Ask about X and listen."

### No action
"You already had a good interaction this week. Nothing is needed."

"No action" is a first-class output.

## Telegram Experience

Telegram is the daily interaction channel, not the source of truth.

Example:

```text
School Coach

You may see Ms. X at pickup today.

One useful question:
"How is Nysha settling into the class socially?"

Why now:
We already have a reasonable academic picture but little of her perspective on social adjustment.

Keep this short. No need to raise anything else today.

[Done] [Not today] [Why?]
```

Afterward:

```text
Did you end up talking with Ms. X?

[Yes] [No]
```

If yes:

```text
Anything worth remembering?

A sentence or voice note is enough.
```

Low-friction feedback is critical.

## Morning Brief

The coach may contribute one item to an N4OS morning brief.

Valid output:

```text
School Coach
Nothing needed today.
```

Do not manufacture a daily task.

## Just-in-Time Coaching

High-value moments include:

- before pickup,
- before parent-teacher conference,
- before back-to-school night,
- before a performance/event,
- before an important teacher interaction,
- after receiving an important school message.

Example:

```text
You're seeing Ms. X shortly.

Goal
Learn how Nysha is adjusting.

Ask
...

Avoid
Turning this into a grade discussion.

Listen for
...
```

## Post-Interaction Capture

After meaningful interactions, collect the minimum useful feedback.

Example user input:

> She seemed rushed but said Nysha participates a lot and she enjoys having her in class.

Possible extraction:

```yaml
interaction:
  channel: pickup
  result: positive

observations:
  - teacher appeared rushed
  - teacher says child participates frequently
  - teacher expressed positive sentiment

belief_update:
  statement: "Pickup may not be ideal for longer conversations."
  confidence: medium
```

Preserve original observations and distinguish them from inference.

## Open Loops

Track unresolved items explicitly.

Examples:

- teacher said she would check on reading groups,
- parent asked about social adjustment but no answer yet,
- teacher mentioned difficulty with transitions and suggested revisiting later.

Conceptually:

```yaml
open_loop:
  topic:
  created_from:
  owner:
  follow_up_window:
  status:
```

## Positive Relationship Balance

The coach should notice when communication becomes problem-dominated.

Do not use a mechanical ratio.

Never invent praise.

Bad:
"Send a generic thank-you because you have not thanked the teacher recently."

Better:
"Nysha mentioned the science project three times this week. This seems like a genuine positive interaction opportunity."

## Important Conversation Preparation

Before parent-teacher conferences or other high-leverage moments, produce a compact prep brief:

```text
Goal
...

What I already know
...

Three questions worth asking
1.
2.
3.

Listen for
...

Useful context to share
...

Probably skip
...

Desired outcome
...
```

Use accumulated context rather than generic question lists.

## User Feedback

Avoid constant ratings.

Collect feedback naturally:

- "Did that help?"
- "How did that go?"
- "Anything worth remembering?"
- accepted / ignored / rejected / modified interventions.

Explicit user corrections should have high weight.

Examples:

> That feels too forced.

> Stop reminding me this often.

> She actually likes talking at pickup.

These should produce durable state updates.

## Weekly Reflection

Internally ask:

- What happened?
- What did I recommend?
- What did the parent do?
- What happened afterward?
- Which assumptions were confirmed?
- Which were contradicted?
- Did I interrupt unnecessarily?
- Did I miss an opportunity?
- What should change next week?

Only surface useful learning.

Example:

```text
One thing I learned this week:

Pickup conversations with Ms. X seem rushed. I'll stop suggesting substantive questions there and reserve pickup for brief interactions.

No action needed from you.
```

## Monthly Review

Review:

- relationship progress,
- intervention quality,
- outcomes,
- parent preferences,
- teacher preferences,
- mistakes,
- unnecessary noise,
- missed opportunities,
- strategy changes.

Maintain a durable Coach Improvement Log.

## Evaluator

Build an evaluator from V1.

Evaluate each meaningful intervention on:

- relevance,
- specificity,
- timing,
- actionability,
- authenticity,
- strategic alignment,
- calibration,
- restraint,
- outcome.

The evaluator should separately judge decision quality and observed outcome.

Example evaluator question:

> Given the same context, objective, history, recommendation, and outcome, would an excellent human relationship coach have acted similarly?

## Golden Scenarios

### A. First week of school

Context:
- new teacher,
- no issue,
- met once.

Expected:
- low-frequency relationship-building,
- curiosity,
- familiarity.

Failure:
- daily message suggestions.

### B. Positive child signal

Context:
- journal says child loved teacher's experiment.

Expected:
- recognize authentic positive interaction opportunity.

Failure:
- generic appreciation unrelated to event.

### C. Teacher appears rushed

Context:
- two short pickup conversations.

Expected:
- tentative belief that longer pickup conversations may be poorly timed.

Failure:
- definitive psychological conclusion.

### D. No meaningful opportunity

Expected:
- no action.

Failure:
- action manufactured to satisfy cadence.

### E. User rejects recommendation

User:
"That feels too forced."

Expected:
- withdraw,
- update preference/belief,
- change future strategy.

Failure:
- generate different wording for the same action.

### F. Conference approaching

Expected:
- synthesize context,
- propose targeted questions,
- identify desired outcome.

Failure:
- generic conference checklist.

### G. Contradicting evidence

Existing belief:
"Teacher prefers email."

New evidence:
Teacher says, "Just grab me at pickup anytime."

Expected:
- lower or replace prior belief.

### H. Coach becomes noisy

Context:
- multiple ignored low-value nudges.

Expected:
- evaluator identifies attention failure,
- coach raises intervention threshold.

## MVP Components

Build only:

1. one coach,
2. one or two relationships,
3. persistent relationship state,
4. observations,
5. beliefs,
6. explicit strategy,
7. interactions,
8. candidate interventions,
9. Telegram delivery,
10. lightweight feedback capture,
11. weekly reflection,
12. basic evaluator,
13. learning log,
14. "show me your plan" experience.

Do not build the complete society-of-coaches framework yet.

## MVP Domain Objects

Suggested conceptual objects:

```text
Coach
Relationship
Observation
Belief
Strategy
Interaction
CandidateIntervention
DeliveredIntervention
Feedback
Outcome
Evaluation
Learning
OpenLoop
```

Some may initially be JSON-backed.

## Suggested Runtime Prompt Composition

Avoid one giant prompt.

Compose from:

```text
Coach Constitution
+
School Coach Mission
+
Current Relationship State
+
Current Strategy
+
Relevant Beliefs
+
Retrieved Context
+
Trigger
+
Structured Output Contract
```

## Example Structured Coach Decision

```yaml
coach_decision:
  observations: []

  belief_updates: []

  relationship_assessment:
    summary:
    confidence:

  strategy_update:
    changed:
    reason:

  intervention_needed:

  candidate_intervention:
    type:
    content:
    rationale:
    confidence:
    urgency:
    expires_at:
    attention_cost:

  follow_up:
    requested:
    prompt:
```

Store concise decision records, not hidden model chain-of-thought.

## MVP Build Phases

### Phase 1 — Persistent coach brain

Build:

- Coach
- Relationship
- Observation
- Belief
- Strategy
- Interaction

Required behavior:

- create a relationship,
- capture observations,
- update beliefs,
- maintain a strategy,
- answer "What is your current plan for this teacher?"

No proactive notifications yet.

### Phase 2 — Intervention loop

Add:

- CandidateIntervention
- DeliveredIntervention
- Feedback
- Outcome
- Telegram delivery.

Required behavior:

- trace recommendation from evidence to rationale to outcome.

### Phase 3 — Reflection + evaluator

Add:

- weekly reflection,
- evaluator,
- learning log.

Required behavior:

- a bad recommendation can visibly change future coaching.

### Phase 4 — Context-aware proactive coaching

Add selected calendar and journal triggers.

Required behavior:

- coach notices useful opportunities without being explicitly asked.

### Phase 5 — Attention management

Add:

- intervention scoring,
- suppression,
- recent-notification awareness.

### Phase 6 — Second coach

Only after real usage.

Use the second coach to validate which abstractions should become shared runtime primitives.

## MVP Quality Bar

Do not declare success because:

- Telegram messages work,
- memory persists,
- cron jobs run,
- LLM responses sound good.

The key proof is:

```text
Coach recommended A.
User rejected it because of X.
System persisted X.
A similar situation occurred later.
Coach behaved differently because of X.
Evaluator can explain the change.
```

That is the core V1 milestone.

## Non-Goals for V1

Do not implement:

- generalized multi-agent orchestration,
- full attention arbiter,
- unrestricted coach-to-coach communication,
- complex relationship scoring,
- gamification,
- auto-sending teacher messages,
- large dashboards,
- aggressive real-time monitoring,
- autonomous prompt rewriting.

## Product Test

Before adding a feature, ask:

> Does this make the coach better at understanding, deciding, acting, learning, or eventually coordinating?

If not, defer it.
