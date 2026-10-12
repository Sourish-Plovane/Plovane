# Agent definitions

The AI agents from the Multi-Agent Dev Workflow Spec (Oct 10, 2026). One file
per agent; this README is the folder's single source of truth for the standards
every agent shares, so the rules are stated once and each agent reads them
rather than carrying its own drifting copy.

| File | Agent | Job |
| --- | --- | --- |
| `main.md` | Main | Classifies each request Frontend or Backend, checks for duplicates, opens issues, maintains `Log.md` |
| `backend-ai.md` | Backend Ai | Triages every `Backend` issue, sizes it, resolves small ones, delegates large ones |
| `backend-engineer-1.md` | Backend Engineer 1 | Executes delegated backend issues |
| `backend-engineer-2.md` | Backend Engineer 2 | Second worker, for overflow or parallel work |

The three people in the roster are GitHub accounts, not agents, and have no
file here: **Aarav-Plovane** (Cofounder, Engineering — every frontend issue),
**Sourish-Plovane** (Founder & CEO — approves ML work and folder changes),
**Nirmal-Plovane** (Cofounder, Systems — co-approves folder changes).

---

## Unified code commenting standard

Every function, in every file and every language, carries this block
immediately above its definition:

```
/**
 * Input: <input variable(s) and types>
 * Output: <output variable(s) and types>
 * Processing: <one line — what this function does>
 * Changed by: <agent or person name>
 * Date: <YYYY-MM-DD>
 */
```

On a later change the block is **updated in place**: `Changed by` and `Date`
move forward, and the other three lines are corrected to match the new
behaviour. It never becomes a stack of old comments stapled on top of each
other, and a stale `Processing` line is a bug like any other.

**Rendering it per language.** The five fields, their wording and their order
are fixed. The `/** */` delimiters are not portable — they are a syntax error
in Python and in CUDA kernels are easy to get wrong around `__global__` — so
each language uses its own comment syntax and keeps the fields identical:

| Language | Delimiters |
| --- | --- |
| JavaScript, C++, CUDA C++, CSS | `/** ... */` exactly as above |
| Python | `#` on each line, directly above the `def`, outside the docstring |
| HTML | `<!-- ... -->` |

Python is the case that matters most right now: `ml/` is entirely Python,
so a literal `/** */` block there would not parse. See *Open questions* below —
this is a deviation from the spec as written, taken so the standard can
actually be applied, and it needs confirming.

## Log.md

Main creates `Log.md` at the repo root the first time it is needed, if it does
not already exist. **No agent creates it pre-emptively.**

Every agent action — an issue opened, assigned, delegated, resolved, or a
folder change requested or approved — appends one line, newest at the bottom:

```
[2026-10-10 14:32] Backend Ai — delegated to Backend Engineer 1 — issue #42 — refactor token-metering queue
```

Format: `[timestamp] <agent> — <action> — issue #<number> — <one-line detail>`

Append-only. **No entry is ever edited or removed** — a correction is a new
line. Appending rather than rewriting is also what makes two agents logging at
once safe: re-read the file and append, never write back a whole buffer you
read earlier, or you will silently drop the lines another agent added in
between.

## Escalation matrix

| Situation | Who approves | Who is assigned |
| --- | --- | --- |
| Duplicate issue found | — (closes automatically) | Main, which closes it with a link to the original |
| Standard frontend bug or feature | none beyond normal review | Aarav-Plovane |
| Backend task, small | none | Backend Ai, resolves it directly |
| Backend task, large | none | Backend Engineer 1 or 2, delegated by Backend Ai |
| ML task (ranking models, training, GPU tokens) | Sourish-Plovane | Sourish-Plovane |
| Folder added or deleted | Sourish-Plovane **and** Nirmal-Plovane | Sourish-Plovane and Nirmal-Plovane |
| Ambiguous or unclear prompt | depends on the content | reassigned, not guessed at |

## Repository governance

- **One README per folder.** New documentation for a folder that already has
  one goes into the existing file — never a second README-like file alongside
  it.
- **Folder changes need permission first.** Creating, deleting or
  restructuring a folder asks *before* the change lands, not after, and a
  folder addition or deletion needs both Sourish-Plovane and Nirmal-Plovane to
  sign off. The one exception: the issue text already explicitly grants
  permission to delete.
- **Unclear instructions get reassigned, not guessed at.** An issue whose
  prompt is ambiguous, or that turns out to touch folder structure once work
  starts, is handed to a better-suited agent rather than carried on with a
  guess.

## Skills

Backend Engineer 1 and 2 run their assigned tasks on the skill Backend Ai
attaches before delegation — the **caveman** skill, or whatever else the task
calls for.

**An agent missing a skill it needs stops and flags it on the issue rather
than improvising a workaround.** This is not a soft preference. An agent that
substitutes its own approach for an unavailable skill produces work nobody
asked for and nobody reviews against a standard.

> **Current state: the `caveman` skill is not installed on this account.**
> Every agent here requires it (see *Open questions*), so as written they will
> all stop and flag on their first task. That is the rule above working
> correctly, not a misconfiguration. Install the skill and they run.

---

## Assumptions and open questions

The spec left six decisions open. Four are answered here with a concrete
default so the agents can run; all four are easy to change, and each is marked
with where to change it. Two are genuinely still open.

**Answered by the spec's author in the request that produced these files:**

1. **Caveman skill scope.** The spec scoped it to Backend Engineer 1 and 2 and
   asked whether it covers every backend task. It is now applied to **all four
   agents, on all tasks**. Each agent's definition opens by loading the skill;
   change the scope by editing that instruction in the agent's own file.

**Defaulted, needs confirming:**

2. **Repository.** Assumed `Sourish-Plovane/Plovane`, the only repository this
   workflow has been set up against. Labels, issue templates and branch rules
   still need creating there — no agent does that itself.
3. **Size threshold.** Defined in `backend-ai.md` as: small means one file, no
   new dependency, no change to a public interface or schema, no migration,
   and verifiable by tests that already exist. Anything failing any of those is
   large. Chosen because every clause is checkable from the diff rather than
   estimated, so two agents sizing the same issue agree.
4. **Reassignment path.** Defined as: an engineer hitting an ambiguous or
   out-of-scope instruction returns the issue to Backend Ai; Backend Ai
   re-sizes or re-routes; anything Backend Ai cannot classify, and anything
   Main cannot classify as Frontend or Backend, goes to Sourish-Plovane. No
   agent reassigns to itself, and nothing is silently dropped.
5. **Duplicate detection.** Defined in `main.md` as a judgment call by Main
   over a full-text search of open *and* closed issues, on same-root-cause
   rather than same-wording. Deliberately not an exact or fuzzy title match:
   auto-closing on title similarity closes distinct issues that happen to be
   worded alike, and that failure is invisible — the reporter is simply never
   answered.
6. **Log.md schema.** Using the spec's one-line format verbatim, as above.

**Still open, not guessed at:**

- **Nested delegation.** Backend Ai is specified as delegating to Backend
  Engineer 1 or 2. Whether it can spawn them directly depends on the runtime;
  where it cannot, it records the delegation decision and the orchestrator
  carries it out. `backend-ai.md` handles both, but which path is live here has
  not been verified.
- **Who runs Main.** The spec gives Main no GitHub identity — it "opens issues
  on others' behalf". Which account's credentials it actually uses, and
  therefore who appears as the author on every issue it opens, is unresolved.
