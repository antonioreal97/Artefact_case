# Pedido: contato incorreto e verificação correta

Modo: modelo fake determinístico + embeddings hash; não é um LLM real.
Comando: `uv run python -m scripts.demo_conversations`
Data (UTC): 2026-11-01

Execução: ChatService real, catálogo CSV e políticas do seed Empório da Música, banco descartável; consultas ao snapshot local, não a um ERP ao vivo.

Dados do seed são fictícios. Entradas pessoais foram omitidas deste exemplo; somente a resposta final ao cliente mostra os detalhes autorizados, revelados localmente após verificação. Nenhum payload do modelo é exportado.

## Turno 1

**Cliente:** Qual o status do meu pedido? [número informado; e-mail incorreto informado]

**Agente (boas-vindas):**

Olá! Bem-vindo(a) à Empório da Música — sua música começa aqui! Nosso atendimento funciona de segunda a sexta, das 9h às 18h, e aos sábados, das 9h às 13h (domingos e feriados, fechado). Posso ajudar com instrumentos, preços, estoque, pedidos e nossas políticas. Como posso ajudar?

**Agente:**

Não foi possível confirmar os dados do pedido. Verifique o número do pedido e o e-mail ou telefone usado na compra.

- Ferramentas: lookup_order.
- Citações: nenhuma.
- Handoff: nenhum.
- grounded: false.

## Turno 2

**Cliente:** Vou corrigir o contato. [mesmo número informado; e-mail correto informado]

**Agente:**

Situação: entregue.
Rastreamento: BRAB1234567BR
Previsão de entrega: 2025-10-25

- Ferramentas: lookup_order.
- Citações: nenhuma.
- Handoff: nenhum.
- grounded: true.
