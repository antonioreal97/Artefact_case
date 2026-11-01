# Endereço da loja e pergunta fora do escopo

Modo: modelo fake determinístico + embeddings hash; não é um LLM real.
Comando: `uv run python -m scripts.demo_conversations`
Data (UTC): 2026-11-01

Execução: ChatService real, catálogo CSV e políticas do seed Empório da Música, banco descartável; consultas ao snapshot local, não a um ERP ao vivo.

O handoff registra uma solicitação; não comprova que um atendente foi notificado.

## Turno 1

**Cliente:** Qual o endereço da loja?

**Agente:**

1.2 Dados da Empresa

Campo Informação

Razão Social Empório da Música Instrumentos Musicais Ltda.

Nome Fantasia Empório da Música

CNPJ 12.345.678/0001-90

Endereço Rua 14 de Maio, 3200 — Centro, Campo Grande - MS, 79202-333

Telefone / WhatsApp (67) 3341-4444

E-mail contato@empóriodamusica.com.br

Fundação 2008 [S3]

Fontes:
[S3] Políticas da Loja — §1.2 Dados da Empresa, p. 2

- Ferramentas: search_knowledge.
- Citações: [S3] Políticas da Loja, 1.2 Dados da Empresa, página 2.
- Handoff: nenhum.
- grounded: true.

## Turno 2

**Cliente:** Qual é a capital da Islândia?

**Agente:**

Não encontrei informação confiável para responder à sua pergunta.
Solicitei atendimento humano para ajudar com sua solicitação.

- Ferramentas: search_knowledge, request_human_handoff.
- Citações: nenhuma.
- Handoff: no_information.
- grounded: false.
