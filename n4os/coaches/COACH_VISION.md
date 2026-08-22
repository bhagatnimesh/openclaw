# N4OS Coaching Vision

## Purpose

N4OS should evolve into a **society of long-lived personal coaches**.

Each coach has a narrow mission, persistent memory, an explicit strategy, and a learning loop. Coaches share the broader N4OS context but remain specialized and bounded.

Examples may eventually include:

- School Relationship Coach
- Parenting Coach
- Learning Coach
- Health Coach
- Career Coach
- Financial Coach

The first implementation is the School Relationship Coach, but the architecture should not assume there will only ever be one coach.

## Core Product Principle

**Build a coach, not a workflow.**

A workflow executes rules.

A coach:

1. observes relevant context,
2. interprets what matters,
3. maintains beliefs,
4. maintains an explicit strategy,
5. notices opportunities,
6. decides whether intervention is worthwhile,
7. helps the user act,
8. captures what happened,
9. evaluates the quality of its own coaching,
10. changes future behavior based on evidence.

The system should preserve this loop explicitly.

## Theory of Change

Long-lived personal outcomes are usually shaped by many small, well-timed actions.

The coach should optimize for:

- relevance,
- authenticity,
- timing,
- continuity,
- low cognitive load,
- real-world outcomes.

It should not optimize for:

- engagement,
- notification volume,
- streaks,
- message counts,
- activity for its own sake.

Sometimes the correct coaching action is **no action**.

## Society of Coaches

Long term:

```text
N4OS
 ├── Shared Personal Context
 ├── Coach Runtime
 ├── Coach Registry
 │    ├── School Relationship Coach
 │    ├── Parenting Coach
 │    ├── Future Coaches
 ├── Coach Memory
 ├── Objectives / Strategy
 ├── Intervention Candidates
 ├── Feedback + Outcomes
 ├── Evaluator
 └── Attention / Coordination Layer
```

Important principle:

**Many specialized intelligences, one coherent user experience.**

Future coaches should be aware that other coaches exist, but avoid unnecessary agent-to-agent chatter.

Coordination should happen through structured signals, shared objectives, and an N4OS-level attention layer.

## Shared Coach Constitution

Every coach should inherit these principles:

1. Optimize for the user's real-world outcome, not product engagement.
2. Prefer authentic actions over manufactured ones.
3. Respect the user's attention.
4. Sometimes the right action is no action.
5. Distinguish facts, observations, beliefs, and strategies.
6. Maintain a coherent long-term strategy.
7. Learn from outcomes and user feedback.
8. Admit and correct mistakes.
9. Stay within the coach's domain.
10. Preserve human agency.
11. Use personal context only when relevant.
12. Make important decisions inspectable.
13. Do not silently convert inference into fact.
14. Do not autonomously rewrite foundational mission or safety constraints.

## Persistent Coach State

A coach should not rely on chat history alone.

Persist structured representations of:

- Coach
- Objective
- Person
- Relationship
- Observation
- Belief
- Strategy
- Open Loop
- Interaction
- Candidate Intervention
- Delivered Intervention
- Feedback
- Outcome
- Evaluation
- Learning

Not every concept requires its own normalized table in V1. Preserve the conceptual distinctions even if some are stored in JSON.

## Memory Model

Separate at least:

### Event memory
What happened?

### Person memory
What have we learned about this person?

### User preference memory
What works for this user?

### Coach playbook
What seems to work broadly?

### Strategy state
What does this specific objective need next?

Do not duplicate all N4OS raw context into each coach.

Coach-specific state should reference source data where possible.

## Facts vs Beliefs

Example:

```text
Fact:
Parent-teacher conference is October 14.

Observation:
Teacher replied with two short messages.

Belief:
Teacher may prefer concise communication.

Strategy:
Use shorter messages until there is evidence otherwise.
```

Beliefs should carry evidence and confidence.

User corrections should be treated as high-value learning events.

## Core Coaching Loop

```text
OBSERVE
   ↓
ASSESS
   ↓
UPDATE BELIEFS
   ↓
PLAN
   ↓
IDENTIFY OPPORTUNITY
   ↓
DECIDE WHETHER TO INTERVENE
   ↓
RECOMMEND / PREPARE
   ↓
USER ACTS
   ↓
CAPTURE OUTCOME
   ↓
REFLECT
   ↓
LEARN
   ↓
UPDATE STRATEGY
```

This loop is more important than any specific UI.

## Candidate Intervention Contract

Coaches should generate **candidate interventions**, not directly assume that every idea becomes a notification.

Conceptually:

```yaml
candidate_intervention:
  coach_id:
  objective_id:
  target_id:
  type:
  content:
  rationale:
  evidence:
  expected_value:
  confidence:
  urgency:
  expires_at:
  preferred_delivery:
  estimated_attention_cost:
```

This leaves room for a future N4OS attention arbiter.

## Attention as a Scarce Resource

Every proactive intervention consumes attention.

A coach should implicitly ask:

> Is this worth interrupting the user for?

Possible outcomes:

```text
send now
include in brief
defer
store silently
do nothing
```

When multiple coaches exist, N4OS should eventually arbitrate across them.

## Assistant Mode vs Coach Mode

The same coach may operate in two modes.

### Assistant mode
The user already knows what they want.

Example:
"Draft a note to the teacher."

### Coach mode
The system carries the objective and decides whether something is worth doing.

The user should not need to explicitly switch modes.

## Inspectability

The user should be able to ask:

- What is your current strategy?
- Why do you think that?
- What have you learned?
- What did you get wrong?
- Why are you recommending this?
- What changed your mind?

Answer from persisted state and evidence rather than generating a plausible retrospective story.

Do not store hidden chain-of-thought. Store a concise decision record:

```text
What I observed
What I concluded
What I decided
What evidence mattered
How confident I am
```

## Self-Learning

Every meaningful intervention should create a learning opportunity.

Distinguish:

- bad recommendation,
- good recommendation not acted upon,
- good recommendation with unpredictable bad outcome,
- good recommendation with useful outcome.

The coach should maintain a durable improvement log:

```yaml
learning:
  scope:
  previous_belief:
  new_belief:
  evidence:
  confidence:
  behavioral_change:
```

The user should eventually be able to ask:

> How have you changed the way you coach me?

and get a grounded answer.

## Evaluator / Meta-Coach

The evaluator is separate from the main coach reasoning.

It should ask:

- Was intervention warranted?
- Was it grounded in context?
- Was it authentic?
- Was it well timed?
- Did it respect attention?
- Was uncertainty calibrated?
- Was there a better alternative?
- Should the coach have stayed silent?
- What should change in future behavior?

Evaluate both:

### Per-intervention quality
Relevance, specificity, timing, actionability, authenticity, restraint, calibration.

### Longitudinal coaching quality
Is the coach becoming more useful over time?
Is noise decreasing?
Is strategy more coherent?
Is the user developing stronger judgment?
Are real-world outcomes improving?

Do not equate outcome with decision quality. Real-world outcomes are noisy.

## Controlled Self-Improvement

Separate:

```text
Immutable principles
User-specific preferences
Coach hypotheses
Coach playbook
```

The coach can update the latter three where appropriate.

It cannot rewrite its own mission or constitution.

## Context Retrieval

Avoid putting all N4OS data into every prompt.

Use targeted retrieval:

```text
What has happened with this person recently?
What upcoming events create an opportunity?
What relevant observations appeared in journals?
What open loops exist?
```

Then reason over the retrieved evidence.

Access does not mean use.

## Trigger Model

Support:

- scheduled triggers,
- event triggers,
- context triggers,
- user-initiated triggers.

Do not wake every coach for every N4OS event.

Use a lightweight relevance gate first.

## Long-Term Technical Direction

Prefer:

- explicit persisted state,
- deterministic orchestration around LLM calls,
- structured outputs,
- replayable decisions,
- testable prompts,
- clear source provenance.

Avoid:

- giant opaque memory blobs,
- giant prompts with all history,
- autonomous loops with unclear stopping rules,
- premature multi-agent generalization,
- coaches rewriting foundational instructions,
- excessive agent-to-agent conversation.

## Build Strategy

Do not build the entire society of coaches framework first.

Recommended sequence:

```text
one excellent vertical coach
→ observe real usage
→ extract repeated primitives
→ build second coach
→ generalize runtime
→ add coordination
```

The destination matters, but current implementation should remain narrow.

## North-Star Product Experience

The user should eventually feel:

> I do not have to remember to manage this goal. My coach understands what matters, notices the moments worth paying attention to, helps me prepare, learns from what actually happens, and becomes more useful over time.

Not:

> I have another app giving me tasks.

## North-Star Technical Test

If removing historical interactions, feedback, and learning would barely change future recommendations, the system is not yet functioning as a true coach.
