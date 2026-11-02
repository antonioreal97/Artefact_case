# Violões disponíveis até R$ 1.000

Modo: modelo fake determinístico + embeddings hash; não é um LLM real.
Comando: `uv run python -m scripts.demo_conversations`
Data (UTC): 2026-11-01

Execução: ChatService real, catálogo CSV e políticas do seed Empório da Música, banco descartável; consultas ao snapshot local, não a um ERP ao vivo.

## Turno 1

**Cliente:** Quais opções de violões disponíveis custando até R$1000?

**Agente (boas-vindas):**

Olá! Bem-vindo(a) à Empório da Música — sua música começa aqui! Nosso atendimento funciona de segunda a sexta, das 9h às 18h, e aos sábados, das 9h às 13h (domingos e feriados, fechado). Posso ajudar com instrumentos, preços, estoque, pedidos e nossas políticas. Como posso ajudar?

**Agente:**

Yamaha C40 Nylon Natural: R$ 599,90.
Yamaha C70 Nylon Natural: R$ 849,00.
Tagima Memphis AC-39 Nylon Natural: R$ 429,90.
Giannini GN-15 Nylon Cedr Natural: R$ 689,00.
Rozini RC-104 Nylon Estudante: R$ 459,90.

- Ferramentas: search_products.
- Citações: nenhuma.
- Handoff: nenhum.
- grounded: true.

## Turno 2

**Cliente:** Qual o preço do Yamaha C40?

**Agente:**

Yamaha C40 Nylon Natural: R$ 599,90.

- Ferramentas: search_products.
- Citações: nenhuma.
- Handoff: nenhum.
- grounded: true.
