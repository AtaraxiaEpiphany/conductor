# Harness Engineering: Complete Reference

Sources:
- [Anthropic - Effective harnesses for long-running agents](https://www.anthropic.com/engineering/effective-harnesses-for-long-running-agents)
- [OpenAI - Harness engineering: leveraging Codex in an agent-first world](https://openai.com/index/harness-engineering/)
- [Martin Fowler - Harness Engineering](https://martinfowler.com/articles/exploring-gen-ai/harness-engineering.html)

---

## 1. Core Problem

### 1.1 What is Harness Engineering?

A **harness** is the system of tools, constraints, and processes built around AI agents so they can consistently produce high-quality, maintainable code across long-running, multi-session projects.

> **Humans steer. Agents execute.**

### 1.2 Why is it needed?

All three sources converge on the same pain points:

| Pain Point | Manifestation |
|---|---|
| Context loss | Each session starts with no memory; the agent must reconstruct state from scratch |
| Premature completion | Agent examines existing code and declares "done" when features are missing |
| One-shotting | Agent attempts the entire application in a single session, exhausting the context window mid-implementation |
| Code drift (entropy) | Agent replicates existing patterns—including bad ones—inconsistency compounds over time |
| Verification gap | Agent marks features complete without end-to-end testing |

---

## 2. Three Pillars (Martin Fowler Framework)

Martin Fowler groups the OpenAI harness components into three categories:

1. **Context Engineering** — Continuously enhanced knowledge base + agent access to dynamic context (observability, browser navigation)
2. **Architectural Constraints** — Monitored by LLM-based agents AND deterministic custom linters + structural tests
3. **Garbage Collection** — Background agents that periodically find inconsistencies, fight entropy and decay

---

## 3. Pillar 1: Context Engineering

### 3.1 Give the Agent a Map, Not a Manual

> "Give Codex a map, not a 1,000-page instruction manual."

OpenAI's rationale for rejecting monolithic instruction files:

- **Context is scarce** — giant instructions crowd out task, code, and docs; agent misses key constraints or optimizes for wrong ones
- **Too much guidance = no guidance** — when everything is "important," agents pattern-match locally instead of navigating intentionally
- **Instant rot** — monolithic manuals become graveyards of stale rules; agents can't distinguish current from obsolete
- **Hard to verify** — single blobs resist mechanical checks (coverage, freshness, ownership, cross-links); drift is inevitable

### 3.2 Progressive Disclosure

OpenAI's actual implementation:

- `AGENTS.md` is ~100 lines, acts as a **table of contents** injected into context
- Deeper knowledge lives in a structured `docs/` directory
- Agents start with a small, stable entry point and are taught **where to look next**, not overwhelmed upfront

Knowledge store structure:

- **Design docs** — catalogued, indexed, with verification status and core beliefs
- **Architecture docs** — top-level domain map and package layering
- **Quality docs** — grades per product domain and architectural layer, tracking gaps over time
- **Execution plans** — progress and decision logs, checked into repo as first-class artifacts
- **Technical debt** — versioned, co-located, so agents can operate without external context

### 3.3 Repository as Knowledge System

> "Anything it can't access in-context while running effectively doesn't exist."

- Slack discussions about architecture decisions? If not discoverable by the agent, it's as if they never happened — same as a new hire who doesn't know the history
- Favor "boring" technology — composability, API stability, strong representation in training data
- Sometimes cheaper to have the agent **reimplement subsets of functionality** than work around opaque upstream libraries (e.g., built own map-with-concurrency helper instead of using `p-limit`, integrated with OpenTelemetry, 100% test coverage)

### 3.4 Anthropic's Context Management

**Layered loading strategy:**
- **Always resident** — `AGENT.md` / `CLAUDE.md` core files
- **On-demand** — Skills system
- **Isolated** — Sub-agents (bulk output stays in sub-context, main context gets summary only)
- **No context propagation** — Hooks

**Compression countermeasures:**
- Explicitly declare compression priorities in global memory files (modified files, verification status, TODOs)
- Generate a **handoff file** before session ends: current progress, what was tried, successes/failures, constraints

### 3.5 Feature List File

Anthropic's approach:

- Initializer agent generates 200+ feature descriptions, all marked `passes: false`
- Coding agent can only change the `passes` field — **never delete or modify tests**
- Uses **JSON format** (not Markdown) — experiments show the model is less likely to inappropriately change JSON files

```json
{
  "category": "functional",
  "description": "New chat button creates a fresh conversation",
  "steps": [
    "Navigate to main interface",
    "Click the 'New Chat' button",
    "Verify a new conversation is created",
    "Check that chat area shows welcome state",
    "Verify conversation appears in sidebar"
  ],
  "passes": false
}
```

> "After some experimentation, we landed on using JSON for this, as the model is less likely to inappropriately change or overwrite JSON files compared to Markdown files."

### 3.6 Agent Legibility

Making the application itself readable by agents:

- App bootable **per git worktree** — each Codex change gets its own isolated instance
- Chrome DevTools Protocol wired into agent runtime — DOM snapshots, screenshots, navigation
- Observability tools exposed to agent — LogQL for logs, PromQL for metrics
- Ephemeral observability stack per worktree — torn down when task completes
- Single Codex run can work **6+ hours** (often while humans sleep)

Prompts become tractable with this context:
- "Ensure service startup completes in under 800ms"
- "No span in these four critical user journeys exceeds two seconds"

### 3.7 Doc-Gardening Agent

- Dedicated agent scans for stale or obsolete documentation that doesn't reflect real code behavior
- Automatically opens fix-up PRs
- Knowledge base freshness validated by CI jobs and linters

---

## 4. Pillar 2: Architectural Constraints

### 4.1 Core Principle

> "By enforcing invariants, not micromanaging implementations, we let agents ship fast without undermining the foundation."

> "Enforce boundaries centrally, allow autonomy locally."

### 4.2 Strict Layered Architecture

OpenAI enforces a fixed layering per business domain:

```
Types → Config → Repo → Service → Runtime → UI
```

- Dependencies flow **forward only**
- Cross-cutting concerns (auth, connectors, telemetry, feature flags) enter through a single explicit interface: **Providers**
- Everything else is disallowed — **enforced mechanically**

### 4.3 Custom Linters

**Critical design: linter errors include remediation instructions, not just "rule violated."**

Error message = what rule was violated + how to fix it — a single message **closes the entire feedback loop** and injects directly into agent context.

What gets enforced:
- Dependency direction checks
- Layer boundary validation
- Structured logging enforcement
- Naming conventions (schema and type)
- File size limits
- Platform reliability requirements

> "In a human-first workflow, these rules might feel pedantic. With agents, they become multipliers: once encoded, they apply everywhere at once."

### 4.4 Constraint Philosophy

- Be strict at boundaries, give agents freedom within boundaries
- Generated code doesn't need to match human stylistic preferences — as long as it's correct, maintainable, and legible to future agent runs
- Human taste feeds back into system continuously — review comments, refactoring PRs, user-facing bugs → documentation updates or directly encoded into tooling
- **When documentation falls short, promote the rule into code**

---

## 5. Pillar 3: Garbage Collection (Entropy Fighting)

### 5.1 The Problem: AI Slop

> Agents replicate existing patterns — including uneven or suboptimal ones. Over time, this inevitably leads to drift.

**OpenAI's real experience:**
- Team spent every Friday (~20% of the week) manually cleaning up "AI slop"
- This did not scale

### 5.2 Golden Principles + Background Cleanup Agents

**Golden Principles** encoded into the repository:

1. **Prefer shared utility packages over hand-rolled helpers** — keeps invariants centralized
2. **No YOLO-style data probing** — validate at boundaries or rely on typed SDKs; agent can't accidentally build on guessed shapes

**Background Codex tasks (regular cadence):**
- Scan for deviations and violations
- Update quality grades
- Open targeted refactoring PRs
- Most reviewable in under a minute and automerged

### 5.3 Continuous Debt Paydown

> "Technical debt is like a high-interest loan: it's almost always better to pay it down continuously in small increments than to let it compound and tackle it in painful bursts."

Human taste is captured **once**, then enforced **continuously** on every line of code. Bad patterns are caught and resolved daily, not left to spread for weeks.

---

## 6. Anthropic's Dual-Agent Architecture

### 6.1 Initializer Agent

Runs **only in the first session**:

- Sets up project scaffolding and repository structure
- Writes `init.sh` script (starts dev server)
- Generates complete `feature_list.json`
- Creates `claude-progress.txt` (progress file)
- Creates initial git commit

### 6.2 Coding Agent

Runs in **every subsequent session**, standard flow:

```
1. pwd — confirm working directory
2. Read git log and progress file — understand history
3. Read feature_list.json — find highest-priority incomplete feature
4. Run init.sh to start dev server
5. Run basic E2E test with Puppeteer — confirm current state is sound
6. Select a single feature to implement
7. Incremental git commit + update progress file
8. Self-verify before marking passes: true
```

Typical session opening:

```
[Assistant] I'll start by getting my bearings...
[Tool Use] <bash - pwd>
[Tool Use] <read - claude-progress.txt>
[Tool Use] <read - feature_list.json>
[Assistant] Let me check the git log...
[Tool Use] <bash - git log --oneline -20>
[Assistant] Now let me check init.sh and restart the servers...
<Starts development server>
[Assistant] Let me verify fundamental features are working...
<Tests basic functionality>
[Assistant] Now let me review what needs to be implemented next...
<Starts work on a new feature>
```

### 6.3 Failure Modes → Solutions Mapping

| Problem | Initializer Behavior | Coding Agent Behavior |
|---|---|---|
| Declares victory too early | Creates feature list file | Reads list, picks single feature per session |
| Leaves bugs / undocumented progress | Initial git repo + progress file | Reads progress + git log, starts server and tests |
| Marks features done prematurely | Creates feature list file | Self-verifies all features; only marks passing after careful testing |
| Wastes time figuring out how to run app | Writes `init.sh` | Reads `init.sh` at session start |

---

## 7. Verification and Feedback

### 7.1 Verification Hierarchy

> "An agent saying it's done means nothing — you must verify it actually did it right."

| Level | Method | What it checks |
|---|---|---|
| **L0** | Exit codes, Linter (SonarQube), TypeCheck | Static correctness |
| **L1** | Unit tests, integration tests | Behavioral correctness |
| **L2** | Browser automation (Puppeteer MCP) | End-to-end user perspective |
| **L3** | Logs, metrics, traces (observability) | Runtime behavior |
| **L4** | Human review | Final judgment |

### 7.2 Key Principles

**Feedback tightness:**
- Tighter feedback loops = cheaper error correction
- Hooks can run compile checks after every edit

**Generation vs. verification asymmetry:**
> Verifying an answer is usually much easier than producing one.

Humans don't need to write code faster than agents — they need to be better at **judging**:
- Define what "done" means
- Identify deviation
- Judge whether direction is correct

### 7.3 Anthropic's Testing Practices

- Agents tend to verify with unit tests or `curl` — can't discover end-to-end breakage
- **Solution:** explicitly prompt agent to use Puppeteer MCP for browser automation testing
- With screenshot capability, agents identified and fixed bugs invisible from code alone
- Known limitation: agents can't see browser-native alert modals through Puppeteer

---

## 8. Orchestration and Cross-Session Coordination

### 8.1 Single vs. Multi-Agent

Anthropic notes this remains an open question: whether a single general-purpose coding agent is optimal, or specialized agents (testing, QA, cleanup) perform better.

### 8.2 Ralph Wiggum Loop

Uses **Stop Hook** to intercept agent exit, forming an automatic loop:

1. Agent generates code
2. Agent reviews its own changes locally
3. Requests additional specific agent reviews (local + cloud)
4. Responds to human or agent feedback
5. Iterates in loop until all agent reviewers satisfied
6. Escalates to human **only when judgment is required**

### 8.3 Sub-agent Context Isolation

- Bulk output stays in sub-agent context
- Main context receives only summaries
- Used for: codebase scanning, running tests, code review

### 8.4 Merge Philosophy

> "In a system where agent throughput far exceeds human attention, corrections are cheap, and waiting is expensive."

- Minimal blocking merge gates
- Short-lived PRs
- Test flakes addressed with follow-up runs, not indefinite blocking
- Irresponsible in low-throughput environments, but the right tradeoff at agent scale

---

## 9. Key Metrics and Results

### OpenAI's 5-Month Experiment

| Metric | Data |
|---|---|
| Lines of code | ~1,000,000 |
| Development time | ~1/10th of manual equivalent |
| PRs opened/merged | ~1,500 |
| Team size | Started 3 → grew to 7 |
| Throughput | 3.5 PRs/engineer/day (and increasing) |
| Manually-written code | **0 lines** |
| Single agent run duration | 6+ hours sustained |

### What Agents Produce

Everything in the repository:

- Product code and tests
- CI configuration and release tooling
- Internal developer tools
- Documentation and design history
- Evaluation harnesses
- Review comments and responses
- Scripts that manage the repository itself
- Production dashboard definition files

---

## 10. Increasing Levels of Autonomy

OpenAI's repository recently crossed a threshold where Codex can **end-to-end drive a new feature** from a single prompt:

1. Validate current state of codebase
2. Reproduce a reported bug
3. Record a video demonstrating the failure
4. Implement a fix
5. Validate the fix by driving the application
6. Record a second video demonstrating the resolution
7. Open a pull request
8. Respond to agent and human feedback
9. Detect and remediate build failures
10. Escalate to human only when judgment is required
11. Merge the change

> "This behavior depends heavily on the specific structure and tooling of this repository and should not be assumed to generalize without similar investment — at least, not yet."

---

## 11. Role Redefinition

### 11.1 The Engineer's New Job

> "The primary job of our engineering team became enabling the agents to do useful work."

Engineers no longer write code. They:

1. **Design environments** — scaffolding, tools, abstractions
2. **Specify intent** — translate user feedback into acceptance criteria
3. **Build feedback loops** — testing, validation, review, recovery

When the agent struggles, it's treated as a signal: identify what's missing (tools, guardrails, documentation) and feed it back into the repository — always by having Codex itself write the fix.

### 11.2 Human Judgment Value

> "When models are already smart enough, the engineer's value becomes judgment."

- AI excels at: generation and execution
- Humans excel at: defining what's correct, weighing trade-offs in ambiguous areas
- Humans judge and design, AI executes and verifies, connected into a feedback loop through the harness

### 11.3 Iterative Harness Construction

**Progressive build-up:**

1. **Start** — pre-commit hooks, basic linting, CLAUDE.md
2. **Enhance** — custom linters, structured tests, knowledge base
3. **Automate** — background cleanup agents, doc-gardening, quality grading
4. **Self-govern** — agent end-to-end feature driving, auto-review, auto-merge

**Key iteration principle:**

> "When the agent struggles, we treat it as a signal: identify what is missing — tools, guardrails, documentation — and feed it back into the repository, always by having Codex itself write the fix."

**Data format choice:** Based on agent behavior, not human habits — Anthropic experiments found JSON less prone to inappropriate model modification than Markdown.

---

## 12. Martin Fowler's Forward-Looking Questions

### 12.1 Will Harnesses Become the New Service Templates?

Most organizations have 2-3 main tech stacks. Fowler imagines teams picking from harness sets for common application topologies, starting from a golden path, then customizing over time. Questions about forking and synchronization challenges mirror today's service template problems.

### 12.2 Does Runtime Need Constraint for AI Autonomy?

Early AI hype assumed unlimited flexibility — generate in any language, any pattern. But maintainable, AI-generated code at scale requires **constraining the solution space**: specific patterns, enforced boundaries, standardized structures. Trading "generate anything" flexibility for prompts, rules, and harnesses full of technical specifics.

### 12.3 Convergence on Fewer Tech Stacks?

As coding shifts from typing to steering, developer taste matters less. We might choose stacks with good harnesses available and prioritize "AI-friendliness." This may apply not just to tech stacks but to codebase structures and topologies — structures easier to maintain with AI because they're easier to harness.

### 12.4 Pre-AI vs Post-AI Application Maintenance?

For older codebases, retrofitting a harness may not be worthwhile — like running static analysis on a codebase that's never had it and drowning in alerts. A divide may emerge between applications built harness-first vs. those that predate the approach.

### 12.5 What's Your Harness Today?

- Do you have a pre-commit hook? What's in it?
- Do you have ideas for custom linters?
- What architectural constraints would you impose?
- Have you experimented with structural testing frameworks like ArchUnit?

---

## 13. Open Questions and Future Directions

- How does architectural coherence evolve **over years** in a fully agent-generated system?
- Where does human judgment add the **most leverage**, and how to encode it so it compounds?
- How will the system evolve as models become more capable?
- Can the approach generalize beyond full-stack web development to scientific research, financial modeling, etc.?
- Is single general-purpose agent or multi-agent architecture (specialized testing/QA/cleanup agents) better?

> "Our most difficult challenges now center on designing environments, feedback loops, and control systems that help agents accomplish our goal: build and maintain complex, reliable software at scale."
