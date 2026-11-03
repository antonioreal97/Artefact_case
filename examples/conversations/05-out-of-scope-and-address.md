# Store address and an out-of-scope question

Mode: deterministic fake model + hash embeddings; not a real LLM.
Command: `uv run python -m scripts.demo_conversations`
Date (UTC): 2026-11-03

Execution: real ChatService, CSV catalog and Empório da Música seed policies, disposable database; queries use the local snapshot, not a live ERP.

The address shortcut uses deterministic triage and public tenant configuration without a model or RAG call (grounded=false). An unrelated question then reaches the agent and is refused without a human handoff or factual grounding.

## Turn 1

**Customer:** Qual o endereço da loja?

> EN: What is the store address?

**Agent (welcome):**

Oi! Aqui é o Empório da Música. Atendimento de segunda a sexta, das 9h às 18h, e sábado das 9h às 13h (domingo e feriado, fechado). Posso te ajudar com instrumentos, preços, estoque, pedidos e nossas políticas.

Como posso te ajudar?
1. Horário e endereço da loja
2. Status do meu pedido
3. Trocas e devoluções
4. Produtos, preços e estoque
5. Falar com um atendente
Responda com o número ou escreva sua dúvida.

> EN: Hi! This is Empório da Música. We're here Monday to Friday, 9 am to 6 pm, and Saturday 9 am to 1 pm (closed Sundays and holidays). I can help with instruments, prices, stock, orders and our policies.
>
> How can I help you?
> 1. Store hours and address
> 2. My order status
> 3. Exchanges and returns
> 4. Products, prices and stock
> 5. Talk to a staff member
> Reply with a number or write your question.

**Agent:**

Horário: Segunda a sexta, das 9h às 18h; sábado, das 9h às 13h. Domingo e feriados, fechado.
Endereço: Rua 14 de Maio, 3200, Centro, Campo Grande - MS, 79202-333
Telefone / WhatsApp: (67) 3341-4444
E-mail: contato@emporiodamusica.com.br

> EN: Hours: Monday to Friday, 9 am to 6 pm; Saturday, 9 am to 1 pm. Closed Sundays and holidays.
> Address: Rua 14 de Maio, 3200, Centro, Campo Grande - MS, 79202-333
> Phone / WhatsApp: (67) 3341-4444
> Email: contato@emporiodamusica.com.br

- Route: triage:hours.
- Tools: none.
- Citations: none.
- Handoff: none.
- Grounded: false.

## Turn 2

**Customer:** Qual é a capital da Islândia?

> EN: What is the capital of Iceland?

**Agent:**

Posso te ajudar só com assuntos da loja: produtos, preços, estoque, pedidos e nossas políticas.

> EN: I can only help with store topics: products, prices, stock, orders, and our policies.

- Route: agent.
- Tools: search_knowledge.
- Citations: none.
- Handoff: none.
- Grounded: false.
