# N4OS Command Map

This maps the supported user-facing Telegram commands and command-like inputs.
The bot also accepts natural language for most domains; slash commands are for
precision and routing.

## Top-Level Help And Session Controls

| Command | Purpose |
| --- | --- |
| `/start` | Show general N4OS Telegram help. |
| `/help` | Show general N4OS Telegram help. |
| `/help <topic>` | Show help for a supported topic, such as `task`, `remember`, `event`, `shop`, or `homework`. |
| `how do I ...`, `how to ...`, `what command ...`, `commands`, `help` | Natural-language help lookup. |
| `/new`, `/reset`, `new session` | Start a new N4OS conversation session and clear current router state. |
| `/chat help` | Show N4OS ask/chat usage help. |
| `/chat reset`, `/chat stop`, `/chat clear` | Clear rich `/chat` history and start fresh. |
| `undo` | Undo the last undoable action, or cancel a pending domain clarification. |
| `cancel`, `cancel that`, `cancel last` | Cancel pending domain/import/homework flows when one is active. |

## Explicit Route Commands

These slash aliases route directly into a domain claw.

| Domain | Slash aliases | Supported actions |
| --- | --- | --- |
| Capture | `/capture`, `/note`, `/mem`, `/mem-inbox` | Capture family observations, journal notes, and Markdown learning notes. |
| Calendar | `/calendar`, `/calender`, `/calnedar`, `/event`, `/schedule` | Create, list, move/update, delete/cancel events, add guests, family briefings, preparation checklists, and Nysha timetable photo updates. |
| Tasks | `/task`, `/tasks`, `/todo`, `/todos` | Create, recommend/list, update, complete, delete tasks, run Noah assistant help. |
| Shopping | `/cart`, `/shop`, `/shopping` | List lists/items, add, check off, uncheck, delete, move, or clear shopping items. |
| Home Board | `/home`, `/homeboard`, `/home-board` | Add/list home board items, bulk add items, mark items done. |
| Decisions | `/decision`, `/decisions`, `/backlog` | Add backlog items, list backlog/decisions, add notes/options/evidence/next steps, position/move/pin/park/close items, record decisions. |
| Science Lab | `/science`, `/science-lab`, `/experiment`, `/experiments` | Plan science experiments and materials. |
| Library | `/library`, `/reading` | Record reading, record checkout, update/delete reading, status, clarify/not-counted flows. |

## Direct N4OS Modes

| Command or phrase | Purpose |
| --- | --- |
| `/ask <question>` | Memory-backed N4OS advice with a knowledge preview and high-level reasoning summary. |
| `/n4os <question>` | Alias for N4OS advice. |
| `/coach <question>` | Alias for N4OS advice. |
| `/advice <question>` | Alias for N4OS advice. |
| `/chat <message>` | Start or continue a richer N4OS coaching conversation with a turn-by-turn knowledge preview and high-level reasoning summary. |
| `/research <question>` | Search current public web sources, then combine the cited evidence with N4OS memory in a separate non-web synthesis step. Uses balanced mode by default. |
| `/research fast <question>` | Use lower-latency web research with low reasoning effort. |
| `/research deep <question>` | Use the strongest research profile with high reasoning effort. |
| `/research help`, `/help research` | Explain when to use Fast, Balanced, or Deep and how research handles sources, reasoning visibility, and N4OS memory. |
| `/review day`, `/review week`, `/review month` | Review recent N4OS patterns without changing stable memory. |
| `/goals` | Show current N4OS goals. |
| `what are my current goals?` | Natural-language goals status. |
| `/status family`, `/status Nysha`, `/status Navya`, `/status goals`, `/status reading` | Show N4OS or reading status. |
| `reading status`, `/reading status`, `garden status`, `/garden status` | Reading Garden status aliases. |

## Capture And Memory

| Command or phrase | Purpose |
| --- | --- |
| `capture <note>` | Capture observations or journal-worthy notes. |
| `capture` followed by dated lines | Batch capture observations. |
| `/capture homework <details>` | Route homework capture before general N4OS capture. |
| `/note quick <text>` | Save a quick Markdown learning note. |
| `/note learning <text>` | Save a longer learning note. |
| `/note inbox <text>` | Save a Markdown inbox note. |
| `/remember <fact>` | Store a structured memory fact. |
| `/remember recent`, `/remember last 7 days`, `/remember last 6 months` | Inspect structured memory from a recent or custom window. |
| `What do you remember about <topic>?` | Query structured memory. |
| `find memory everywhere <topic>` | Search structured memory plus selected N4OS family, journal, trajectory, playbook, and top-level memory files. |
| `update remembered note <topic> to <value>` | Update a structured memory item. |
| `forget remembered note <topic>` | Delete a structured memory item. |
| `fix last capture: <correction>` | Correct the most recent single saved capture. |
| Reply to an ask/chat/research knowledge, evidence, or reasoning message with `capture: <feedback>` | Save linked N4OS tuning feedback with the originating answer trace. |
| Reply to a capture with `replace "old" with "new"` | Correct that saved capture. |
| Reply to a capture with `<new>, not <old>` | Correct that saved capture. |

## Imports

| Command | Follow-ups |
| --- | --- |
| `/import school newsletter for Nysha <Google Slides link>` | Preview school newsletter updates. Reply `save` to apply or `cancel` to drop. |
| `/import second brain <link>` | Preview reusable N4OS Markdown import. Reply `save`, `adjust: <changes>`, or `cancel`. |
| `/import n4os <link>` | Alias accepted by the second brain importer. |

## Common Natural-Language Routes

These are supported without slash commands when the intent is clear.

| Domain | Examples |
| --- | --- |
| Calendar | `add event dentist tomorrow at 4 PM`, `move dinner to Saturday`, `show tomorrow's calendar`, `give me today's briefing`, `Update Nysha school calendar from this image` |
| Tasks | `add task call FUSD tomorrow morning`, `complete task call FUSD`, `delete task call FUSD`, `show urgent tasks due this week` |
| Homework | `homework status`, send a homework photo with a homework caption, `cancel` for pending duplicate prompts |
| Shopping | `add milk to Costco`, `Indian grocery done`, `what's on my Whole Foods list?` |
| Reading Garden | `Nysha read 8 pages of Mercy Watson by herself`, `library checkout: Mercy Watson, Frog and Toad`, `Delete Nysha latest reading entry` |
| Science Lab | `plan the next 4 science lab experiments`, `what materials do we need for science lab?` |
| Home Board | `add home board item buy milk`, `before we leave, take jackets and snacks`, `show today at home` |
| Decisions | `Discussion: Should we attend the birthday?`, `Planning: Camping trip September 12`, `Decision: Choose Nysha's school next year`, `decision brief for summer camp` |

## Calendar And Task Interpretation

Text, voice transcripts, and photo text use the same intent pipeline. Voice and
photos may omit the slash command; N4OS uses the original image when available
and treats OCR or transcription as imperfect evidence.

- Relative dates such as `tomorrow`, `next Friday`, and `next week` resolve in
  the configured family timezone.
- Calendar requests can name a writable calendar. Task requests can name a
  Google Tasks list. Names must match one available destination uniquely.
- Recurring calendar phrases such as `every Thursday` become Google Calendar
  recurrence rules. Multiple explicit dates are previewed as one batch.
- To update Nysha's weekly school schedule, send a clear timetable photo with
  the caption `Update Nysha school calendar from this image.` N4OS previews the
  parsed additions, changes, and removals before writing them. Reply `yes` to
  apply, `cancel` to discard, or send a correction for an unclear row.
- Unclear or clipped timetable rows require correction rather than a guess.
  Unrelated calendar events are preserved.
- Clear, single typed creates may be saved immediately. Voice, photo, bulk,
  destructive, or otherwise uncertain writes show a preview or confirmation
  first. Reply `yes`, `no`, or give a correction such as `Monday instead`.
- If semantic interpretation is unavailable, high-confidence local parsing is
  used. Missing or ambiguous fields produce one focused question; they do not
  trigger a guessed write.

## Handler Precedence

Telegram handling checks commands in this order:

1. `/start`, `/help`, and slash help such as `/task help`.
2. `undo` / `cancel` flows.
3. Capture correction replies.
4. Import previews and import follow-ups.
5. Structured memory status, mutation, remember, and query flows.
6. N4OS status, goals, review, research, chat, and advice modes.
7. Help/how-to questions.
8. Domain routing through the N4OS intent router.

Source references: `telegram_bot.py`, `claws/n4os/routing_contracts.py`,
`claws/n4os/second_brain_importer.py`, and `claws/n4os/school_newsletter.py`.
