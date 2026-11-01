# Preço e estoque do Takamine GD20

Modo: modelo fake determinístico + embeddings hash; não é um LLM real.
Comando: `uv run python -m scripts.demo_conversations`
Data (UTC): 2026-11-01

Execução: ChatService real, catálogo CSV e políticas do seed Empório da Música, banco descartável; consultas ao snapshot local, não a um ERP ao vivo.

## Turno 1

**Cliente:** Quanto custa o Takamine GD20?

**Agente:**

Takamine GD20 Dreadnought Natural: R$ 2.199,00.

- Ferramentas: search_products.
- Citações: nenhuma.
- Handoff: nenhum.
- grounded: true.

## Turno 2

**Cliente:** Takamine GD20 disponível em estoque?

**Agente:**

Takamine GD20 Dreadnought Natural: R$ 2.199,00.
Takamine GD20 Dreadnought Natural: disponível em estoque.

- Ferramentas: search_products, check_stock.
- Citações: nenhuma.
- Handoff: nenhum.
- grounded: true.
