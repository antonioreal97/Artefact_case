# Live English conversations (real model)

Supplementary evaluation evidence, separate from the five official interaction examples in [examples/conversations](../../../examples/conversations/README.md).

These conversations use English customer messages sent through the real gpt-5-mini Docker API under prompt version `commerce-privacy-v9`. Each recording attempt is a one-off, non-deterministic observation. Tests check artifact structure and privacy only, never replay paid calls or assert model quality. JSON companions preserve the six recorded API fields verbatim; customer contact and order inputs are omitted. Previous v8 recordings and alternate v9 attempts are kept under [attempts/](attempts/).

What these v9 recordings show (honestly):

- Catalog A (attempt 2): budget question with `R$1,000` stayed grounded; price and stock follow-ups grounded in English; a short Portuguese fragment still appeared once; the search hit set can be narrower than a Portuguese phrasing.
- Return/order/scope B (attempt 3): natural English return question retrieved and cited the manual without coaching; the wrong contact ended in a `no_information` handoff instead of a request to check the contact; the correct contact verified the order and revealed tracking and expected date (field labels and the closing question in Portuguese); Iceland used the English `OUT_OF_SCOPE_EN` refusal without handoff. Attempts 1–2 (archived) failed on the order turns because earlier recordings' deliberate wrong contacts had locked order 1 (three failed verifications within one hour → `privacy_guard`); attempt 2 also missed retrieval, and its English admission correctly took `no_information`.

The tenant welcome remains Portuguese (configured copy; not a per-language tenant setting). A handoff is a persisted request, not proof that a human was notified. Grounded metadata is the application's lexical assessment, not an independent guarantee of every factual claim.
