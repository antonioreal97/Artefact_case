# Limitations and next steps

Full limitations list for the Empório da Música agent. The root
[README](../README.md#limitations) highlights the five most relevant for reviewers;
this page keeps every caveat previously documented there.

## Quality and grounding (A-2/A-4, O-4)

The Portuguese live battery reached 24/27 useful answers on R9 native after the
English-customer fixes and again on R10 native after the humanized-tone changes
(23/27 on R11 with `OPENAI_REASONING_EFFORT=low`), with earlier rounds between 19
and 23; evaluation with held-out questions, more multi-turn conversations and
conflicting sources is still missing. Output rules are lexical: they reduce
fabrication but also reject correct answers. A citation or product name does not
prove stock, lead-time or condition consistency. Critical facts should be rendered
locally from typed evidence, with contradiction tests added.

## English / multilingual customers

Live English re-recordings under v9 can answer a natural return-policy question with
citations and refuse out-of-scope questions in English, and the budget catalog no
longer fails solely on `R$1,000` formatting. Remaining limits: the tenant welcome
stays Portuguese; replies can still mix languages (labels of locally revealed order
fields and closing questions came back in Portuguese); a wrong contact ends in a
`no_information` handoff instead of asking the customer to check it; three wrong
contacts lock the order for an hour for everyone, including the real customer, so
anyone who knows an order number can trigger it (next step: key the lockout to the
actor and alert instead of blocking the order); catalog breadth and retrieval still
vary by phrasing; offers of tool-less actions (starting a return, quoting freight)
are prompt-only, enforced only by the success-claim check when the model claims the
action happened. Broader multilingual evaluation is still open.

## Style

The ESTILO rules of `commerce-privacy-v11` (answer first, short, no greeting loops, light
citations) are prompt-only, so live replies still sometimes use em dashes, long policy
answers or a templated closing question.

## Latency

A reply takes several seconds because each turn makes 2–3 sequential `gpt-5-mini`
calls (choose a tool, read its result, answer); catalog tools take milliseconds and
knowledge search about 2s. On the 27-turn battery (2026-11-03),
`OPENAI_REASONING_EFFORT=low` measured median 9.3s, p90 12.0s, max 27.6s, mean 9.4s
(provider default: 11.8s / 17.7s / 44.2s / 11.9s). There is no streaming, so the
customer waits for the whole answer. `minimal` (median 4.0s) cost answer quality.
Next steps: stream the final answer, and answer simple policy questions with one
model call.

## Money written in words

Price validation reads digits only (as on `main`). A budget written in words
("até mil reais") is not exempt, so an answer that repeats it as "até R$1.000"
falls back to a human; a price written only in words ("custa mil reais") is not
checked at all, and "mil e 500 reais" in a document is read as R$500. These cases
are `xfail` tests in `tests/agent/test_humanized_tone_review_fixes.py`. A
number-word parser was tried on a branch, but each review round found a new
false-price bypass, so the behavior was reverted to `main`'s and kept documented.

## Privacy (A-3, O-9)

Unknown lowercase names and contextual identifiers may escape the rules. Tokens
enable linkage within a conversation. The external boundary should be restricted to
structured public/abstract inputs with ambiguous text kept local; neither
anonymization nor universal coverage is claimed.

## Concurrency (A-5)

Vault GET/SET and turn commits are not atomic; simultaneous requests or retries may
lose state or duplicate actions. Per-conversation serialization and idempotency are
needed before concurrent use.

## Scope (A-6)

The platform exceeds the need demonstrated by the case. Extra features should stay
optional, without a rewrite just before delivery. See
[platform extensions](platform/extensions.md).

## RAG (A-7/A-8)

Immutable processing generations, atomic index swaps, calibration with real
embeddings and condition/conflict evaluation are missing. Scanned PDFs have no OCR;
tables and complex layouts have limited support. The manual has ambiguous contacts
and installment rules: they are not reconciled by assumption.

## Code and scale (A-9/A-10)

The chat service concentrates responsibilities; several requests load a tenant's
entire customer/product sets. Evidence/validation/rendering should be refactored,
and memory, cost and p95 measured before scaling beyond the seed's 50 customers and
65 products.

## Authorization and operations (A-11/A-12)

Tenant filters are a tested convention, without RLS; end users are not bound to HTTP
sessions. Queues and WhatsApp lack durable delivery guarantees. Identity,
inbox/outbox, retries and per-tenant credentials should be prioritized; Redis in
Compose has no durable volume.

## Retention and observability (A-13/A-14)

Purge exists, but conversation activity and document lifecycles are incomplete.
Usage/cost and effective-configuration metrics are missing; current metrics are per
process.

## False positives (O-10)

Correct denials of a promotion can be blocked by the lexical rule; R5 fixed the
specific case where “Custom 24” was read as a price, without resolving every
promotion denial. Partial search can return alternatives sharing terms, without
guaranteeing equivalence to the requested product. Existing seed tenants need to
reapply public configuration with `scripts.create_tenant` to incorporate missing
contacts/address. Opaque transport IDs and UUIDs in dedicated fields received
specific handling in R4; numbers in free text remain subject to the scanner.
