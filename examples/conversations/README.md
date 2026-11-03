# Conversation examples

The five numbered Markdown files in this directory are deterministic offline demonstrations. They run the real ChatService, tools, privacy boundary and seed data with a scripted fake model and hash embeddings in a disposable database. Customer and agent dialogue stays verbatim in Portuguese; each message, including the tenant welcome, has an English translation beneath it. The surrounding presentation is in English. These are pipeline fixtures, not evidence of real-model language quality.

Regenerate them with Postgres available:

```bash
uv run python -m scripts.demo_conversations
```

Tests regenerate the files twice and compare them except for the date. The generator rejects dialogue without a known English translation, so changed replies require a translation update. Product names, currency values and authorized order details retain their original values. Personal customer inputs are replaced by explanatory placeholders; seed data is fictitious.

This directory holds exactly the five interaction examples requested by the case. Conversations recorded once against the real model with English customers are evaluation evidence and live in [evals/live/english-conversations](../../evals/live/english-conversations/README.md).
