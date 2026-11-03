# Platform extensions (optional)

The delivered solution on this repository's `main` branch extends the case into a
**multi-tenant platform**. The case asks for a messaging agent, rationale, instructions
and examples, solved as simply as possible; multi-tenancy, generic uploads and WhatsApp are
not case requirements. They were built on purpose: to show how an agent meant to scale and
keep running for long periods with little maintenance could treat new stores, channels and
policy documents as configuration and operator actions rather than code changes. A minimal
script would answer the case, but usually needs small edits every time the data, documents
or channels change. This is a design intent, not a measured result: no load or long-running
operation tests were run, and the [limitations](../limitations.md) list what still needs
work before production use. [ADR 005](../adr/005-multi-tenant-platform-branch.md) records
that decision and its heavier runtime compared with the original smaller design.

Tenant isolation, the WhatsApp webhook, document uploads and ingestion jobs, Redis
rate limits, and the `/chat` test UI extend the core agent demonstration. They let the
same service exercise store isolation, additional channels and operator document workflows;
they do not establish a measured advantage over a simpler solution for this case.
Redis also supports the implemented privacy vault and order-lookup lockout; optional
product scope does not mean every infrastructure component can be removed from the
current runtime without changes. The supported walkthrough remains one seeded store
over HTTP.

## Deterministic triage menu (optional UX)

Empório greeting-only first contact includes a numbered menu. A first-contact question gets
the welcome without the menu, the normal answer, and a short hint to type `menu`. Digits are
menu choices only while the previous assistant turn left the menu (or an expecting sub-step)
pending; any agent reply clears that state so `4` after a product list reaches the agent.
Clear purchase intent (`quero comprar`, `vou levar`, …) hands off to sales immediately
(`triage:purchase`) because the agent cannot complete a sale. Customers can also use shortcuts
such as `horário` or `endereço`, or write normally; `menu` and `0` reopen the full menu.
Python answers hours/contact and the short online-return policy, guides verified order
lookup, and creates human handoffs without model calls. Product details, prices, stock,
and specific policy questions go to the agent.

Triage is opt-in through `AgentSettings.extra.triage`; new Empório seeds enable it.
Existing tenants keep their configuration. To explicitly enable/reset the Empório defaults,
run `uv run python -m scripts.create_tenant --refresh-triage`. Pending steps persist in
assistant-message metadata; no migration is needed. See the
[routing table](../customer-service-flows.md#deterministic-triage) and
[contract](contracts.md#t1--deterministic-triage).

This is an optional UX optimization ahead of the agent loop, not part of the core
architecture the case requires.

## Browser test interface

With the API and seed ready, open `http://127.0.0.1:<API_PORT>/chat`
(default `http://127.0.0.1:8000/chat`) and paste the key into the access field.
The quick-start `export SEED_API_KEY=...` command loads the key from `.env`;
prefer `SEED_CHAT_API_KEY`, if provisioned by the seed, loading it the same way.
Use the administrative key only on the local machine. The interface keeps the key
only in `sessionStorage`, within the tab session; clear the field to remove it.

Send questions, continue the conversation, or click **Nova conversa** (“New conversation”).
Each response's details show tools, evidence (`grounded`), sources, latency and any
handoff. One turn is sent at a time. Reloading starts a new conversation;
**Nova conversa** clears the screen without deleting server-side history.

This is a development and demonstration interface: it follows `EXPOSE_API_DOCS`
and returns 404 in production, including its assets. It is not a production widget
and provides no end-user authentication. It uses the provider configured in the API;
the page does not detect whether that provider is fake/hash or a real model.

## Welcome message

Configure `agent_settings.welcome_message` per tenant in the database (optional text,
up to 1,000 characters). The Empório seed uses the manual's hours: Monday–Friday,
9 am–6 pm; Saturday, 9 am–1 pm; closed Sundays and holidays. Rerunning the seed fills
only empty values and preserves operator-defined text.

On first contact in a conversation, a greeting alone receives only that text, without
a model call. If the message already includes a question, the customer receives the
welcome and normal answer without repeating the question. In the latter case, the
API returns `welcome_message` before `reply`; WhatsApp and `/chat` display separate
messages. The welcome stays in history and is not repeated. Tenants without configured
text retain their previous behavior. Refusals, failures and handoffs receive no welcome;
safety and privacy controls stay active. Use only public store text, without personal
data or customer placeholders.
