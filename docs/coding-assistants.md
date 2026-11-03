# Use of coding assistants (full workstream table)

Detailed annex to the root [README disclosure](../README.md#use-of-coding-assistants).

I used **Claude Code (Anthropic)**, **Cursor**, **Hermes** and **OpenAI Codex** in agent
sessions, with parallel terminals managed by **Orca** and local Python environments
to run code, tests and evaluation. I, `antonio-real`, defined the objective and
constraints, chose the platform direction (ADR 005), decided what to accept and
approved every commit. All commits name the owner as author; that authorship does
not mean all code was written manually. Throughout every stage, I also used **Cursor** myself to monitor all files modified by each agent, call the tools I needed and debug the work as it progressed.

Claude Code coordinated the work: it divided workstreams with frozen contracts and
exclusive file ownership, wrote task specifications, answered workers' questions,
reran every check before accepting each delivery and proposed commits.
The tools identified per workstream are:

| Workstream | Tool |
|---|---|
| All stages — monitoring every file modified by each agent, calling the needed tools and debugging throughout development | Cursor (used by the owner) |
| B — infrastructure | Orca, Hermes and Cursor |
| C — RAG | Claude Code and Cursor; the embedding-model choice in PostgreSQL/pgvector is still open |
| D — commerce tools | Claude Code and Cursor; planned channel: WhatsApp |
| E — initial agent implementation | OpenAI Codex |
| F — API (flow validation) | OpenAI Codex |
| R1 — security review | OpenAI Codex |
| E2, E3 — agent fixes | Claude Code |
| F2, F3 — platform fixes | Claude Code |
| R2 — adversarial tests | Claude Code |
| G1 — evaluation | Claude Code |
| R3 — architecture review | OpenAI Codex |
| X1 — example conversations, catalog filtering and stock validation | OpenAI Codex |
| X2 — this README and documentation alignment | OpenAI Codex |
| D1 — complete Docker run | OpenAI Codex |
| L1 — LangChain model layer | OpenAI Codex |
| U1 — browser test interface | OpenAI Codex |
| W1 — welcome message | OpenAI Codex (main implementation) and Cursor (final review, verification and completion) |
| F1 — customer-service flow documentation | Cursor (Composer / Orca worker) |
| R4–R8 — fixes after live-model runs | Claude Code ran the batteries and diagnosed causes; OpenAI Codex implemented the fixes |
| E1 — out-of-scope refusal without handoff, bilingual demo conversations, first English live recordings | OpenAI Codex |
| T — English translation of the documentation and README restructure | OpenAI Codex (README, docs index, service flows, context/evaluation/security docs) and Cursor (process, architecture, ADRs, reviews and test reports) |
| E2 — English-customer fixes (`commerce-privacy-v9`), re-recordings and R9 battery | Cursor |
| Integration of the English delivery and diagnosis of the R9 order failure (lockout from earlier recordings) | Claude Code |
| E3–E3f — humanized store tone (`commerce-privacy-v10`): style rules, warm fixed messages, lighter citations, fixes after each review round | Cursor |
| V3–V3f — independent read-only reviews of the tone work (six rounds; rejected attempts became regression tests; final approval) | OpenAI Codex |
| E4 — configurable reasoning effort and live latency/quality comparison (`low` shipped) | Cursor |
| T1 — deterministic triage menu before the agent | OpenAI Codex (implementation) and Cursor (completion after a usage limit, smoke test) |
| RT1 — independent read-only review of T1 and E4 | Cursor |
| T2 — triage UX fixes from the owner's browser test (no menu after a first-contact question, menu digits only right after the menu, purchase intent to sales) | Cursor (implementation); Claude Code (review and live re-check) |
| Coordination of the tone, latency and triage work, decisions after each review, and integration | Claude Code |
| D2 — README core-first reframe (documentation only) | Cursor (Orca worker) |

The workflow was: **contracts → parallel workstreams → independent review (R1) →
adversarial tests (R2) → evaluation with gates (G1) → fixes by owners (E2/F2, E3/F3) →
independent architecture review (R3) → this README**. Some rounds overlapped; the
history and [development log](development-process.md) show the checkpoints.

Not everything worked on the first attempt. Usage limits interrupted workstreams,
which resumed from checkpoints. The E2 price fix introduced a regression: values
in documents started being accepted as product prices. The adversarial suite found
**FINDING-5**; E3 restricted those values and retained the case as a regression test.
Tests and independent reviews verified the assistants' deliveries; acceptance
remained a human decision.
