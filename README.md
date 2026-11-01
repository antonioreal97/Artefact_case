# Empório da Música — agente de atendimento

Protótipo em Python para atender, em português, os clientes da loja fictícia
Empório da Música, de Campo Grande/MS: consultar instrumentos, preços e estoque,
explicar políticas e consultar pedidos após verificação local de contato.
O tom configurado é informal, profissional e acolhedor: “Sua música começa aqui”.

Esta branch, `test-rag`, é uma **variante de plataforma multi-tenant**. O desafio
pede um agente de mensagens, justificativas, instruções e exemplos; exige Python,
mas deixa interface, framework e persistência a critério do candidato.
Multi-tenancy, uploads genéricos e WhatsApp são **experimentos opcionais**,
escolhidos pelo responsável pelo projeto, não requisitos do caso.

O caminho demonstrado aqui é uma loja, uma API e provedores determinísticos.
**Não houve validação com modelo ou embeddings reais neste ambiente.** Os testes
verificam integração e controles exercitados, não naturalidade ou qualidade de um LLM.
O backend consulta um snapshot importado dos CSVs, não um ERP conectado em tempo real.

- [Executar offline](#executar-offline)
- [Modelo real](#modo-com-modelo-real-opcional)
- [Decisões técnicas](#decisões-técnicas)
- [Exemplos](#exemplos-de-interação)
- [Testes e avaliação](#testes-e-avaliação)
- [Limitações](#limitações-e-próximos-passos)
- [Uso de assistentes](#uso-de-assistentes-de-código)

## Executar offline

### Pré-requisitos

Python **3.12**, `uv`, Docker com Compose e `curl`. Execute os comandos na raiz
do repositório; mantenha os dados fornecidos em `Desafio Técnico AI Engineer 2026/data/`.
O `uv run` prepara o ambiente conforme `pyproject.toml` e `uv.lock`; a primeira
execução pode precisar de internet para baixar dependências.

```bash
uv --version
uv run python --version
docker compose version
```

Validado em 2026-11-01 com Python 3.12.13, uv 0.11.18 e Docker Compose v5.5.1.
A configuração completa está em [.env.example](.env.example). O roteiro usa
variáveis de ambiente, sem precisar modificar esse arquivo ou um `.env` existente.
Os valores abaixo são exclusivos de desenvolvimento local.

### Banco, configuração e migrações

```bash
export APP_ENV=dev
export LLM_PROVIDER=fake
export EMBEDDING_PROVIDER=hash
export INGESTION_MODE=inline
export DATABASE_URL=postgresql+asyncpg://emporio:emporio@localhost:5434/emporio
export REDIS_URL=redis://localhost:6380/0
export API_KEY_PEPPER=dev-only-pepper-change-me
export USER_REF_KEY=dev-only-user-ref-key-change-me
export OPENAI_TRACING_ENABLED=false
export RUN_OPENAI_LIVE_TESTS=0

docker compose up -d db redis
uv run alembic upgrade head
```

Postgres/pgvector usa a porta **5434** e Redis a **6380**, publicadas apenas no
localhost. Aguarde os serviços ficarem saudáveis antes da migração se for a
primeira inicialização. `APP_ENV` ausente significa `prod`, que rejeita fake/hash
e os segredos de desenvolvimento. `inline` dispensa iniciar o worker para o roteiro.

### Seed e chave local

Em um banco novo, gere uma chave e mantenha-a nesta sessão:

```bash
export SEED_API_KEY="$(uv run python -c 'from app.core.security import generate_api_key; print(generate_api_key()[0])')"
uv run python -m scripts.seed --api-key-from-env SEED_API_KEY
```

O seed valida os seis CSVs antes da importação e ingere o PDF de políticas:
9 categorias, 65 produtos, 25 promoções, 50 clientes, 20 pedidos e 22 itens.
Ele é idempotente para a mesma chave; não exclui registros ausentes dos CSVs.
Em banco já inicializado, reutilize a chave original: uma chave diferente é
rejeitada, e seu valor não pode ser recuperado a partir do hash persistido.
Guarde-a fora do Git. Não execute novamente a linha de geração para reutilizar o seed.

`SEED_API_KEY` é uma chave **administrativa**, com todos os escopos; use-a apenas
no terminal ou servidor confiável. A opção `SEED_CHAT_API_KEY` provisiona uma
segunda chave com escopo `chat`, mas não resolve a ausência de autenticação por
usuário final na API. Não distribua a chave administrativa em um widget.

### API e primeira mensagem

No mesmo terminal, inicie a API:

```bash
uv run uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Em outro terminal, disponibilize a **mesma** `SEED_API_KEY` gerada acima, sem
imprimi-la em logs. A API de desenvolvimento expõe sua especificação em
`http://127.0.0.1:8000/docs`; em produção a documentação é desativada.

```bash
curl --fail-with-body --silent --show-error http://127.0.0.1:8000/health

curl --fail-with-body --silent --show-error \
  http://127.0.0.1:8000/v1/chat/messages \
  -H "X-API-Key: $SEED_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"message":"Quanto custa o Takamine GD20?"}'
```

Resposta verificada, abreviada:

```json
{
  "reply": "Takamine GD20 Dreadnought Natural: R$ 2.199,00.",
  "sources": [],
  "handoff": null,
  "grounded": true,
  "tools_used": ["search_products"]
}
```

O JSON também contém `conversation_id`: envie esse campo com `message` nas
mensagens seguintes para continuar a conversa. Envie **um turno por vez por
conversa**; não há serialização nem idempotência para turnos concorrentes.
`grounded` indica evidência aceita pelas heurísticas, não verificação de cada fato.
`handoff` representa uma solicitação persistida; não comprova notificação ou resposta humana.

## Modo com modelo real (opcional)

O adaptador implementado usa OpenAI, com defaults locais `gpt-5-mini` para chat e
`text-embedding-3-small` com 1536 dimensões para embeddings. São escolhas de
implementação, sem benchmark comparativo de qualidade, custo ou latência.

Para habilitá-lo, configure `OPENAI_API_KEY` por variável de ambiente ou arquivo
`.env` ignorado pelo Git, selecione `LLM_PROVIDER=openai` e
`EMBEDDING_PROVIDER=openai`, mantendo tracing desligado. As opções
`OPENAI_CHAT_MODEL` e `OPENAI_EMBEDDING_MODEL` permitem configuração explícita.
A escolha do provedor não altera as restrições de [privacidade](SECURITY.md).

**Não basta trocar as variáveis sobre um banco preenchido com hash embeddings.**
Os vetores podem ter a mesma dimensão e espaços incompatíveis. Para uma avaliação
isolada, o harness com opção `--live` cria um banco descartável e indexa com o
provedor selecionado; exige a chave e gera chamadas cobradas. Para a API, use um
banco novo com migrações e seed já configurados para OpenAI, ou reprocesse todos
os documentos com esse provedor antes de servir consultas. Reexecutar apenas o
seed pode pular documentos já concluídos; não há fingerprint de geração dos vetores.

**Esse modo não foi executado neste ambiente.** Não há resultado de modelo real,
calibração dos limiares para embeddings reais ou confirmação operacional dos
controles contratuais de retenção do provedor. A demonstração entregue usa fake/hash.

## Decisões técnicas

| Escolha | Justificativa e alcance |
|---|---|
| Um agente com OpenAI Agents SDK | Sete ferramentas cobrem um único domínio; o loop limitado evita criar agentes especialistas sem necessidade. Um `GuardedModel` inspeciona cada payload antes do provedor, força `store=False` e bloqueia estado hospedado. |
| FastAPI como interface | HTTP permite reproduzir conversas com curl e exercitar autenticação, erros e isolamento; uma interface visual não é necessária para avaliar o agente. |
| OpenAI ou fake por configuração | O SDK integra o adaptador real; fake permite testes sem custo externo e falhas reproduzíveis. Não há adaptadores locais, Anthropic ou Google implementados nesta branch. |
| Tools para fatos operacionais | Preço, estoque, promoção, pedido e cliente vêm de consultas tipadas ao `CommerceBackend`; o modelo não gera SQL nem usa RAG para inferir esses dados. |
| RAG somente documental | Políticas, FAQ e manuais precisam de trechos e referências; o seed contém o PDF da loja. Embeddings não substituem verificações de identidade. |
| Busca híbrida + RRF | pgvector/cosseno captura similaridade e full-text em português captura termos exatos; Reciprocal Rank Fusion combina posições, sem somar escalas incompatíveis. A superioridade sobre busca lexical não foi medida. |
| Suficiência antes de responder | Limiar de similaridade e cobertura lexical retêm fontes insuficientes; consultas filtram tenant, documento ativo e versão atual no SQL. É uma heurística de relevância, não prova de resposta completa. |
| Prompt versionado | `commerce-privacy-v2` combina persona configurável com regras obrigatórias; documentos chegam em `<untrusted_document>`, como dados não confiáveis. Guardrails e autorização continuam em código. |
| CSVs validados + vault | Tipos, referências e unicidade são validados; importação usa upsert por tenant. Dados restritos ficam locais; spans reconhecidos viram tokens reversíveis, resolvidos por ferramentas locais. Isso é pseudonimização, não anonimização. |
| Histórico persistido | Postgres guarda conversas, mensagens originais e redigidas, fontes e execução de ferramentas; apenas a forma redigida segue ao modelo. Redis guarda o vault com TTL móvel de 24 horas. |

Fluxo de um turno:

```text
API autenticada → tenant → classificação/tokenização local → agente limitado
   ├─ catálogo, estoque, pedidos → ferramentas → banco local
   └─ políticas → busca híbrida → trechos não confiáveis + citações
→ validação de saída → revelação local autorizada → resposta + persistência
```

Os dados do caso são fictícios, mas tratados como pessoais; não são exceção à
regra de impedir PII bruta em modelos externos e logs. A detecção usa regras e
valores conhecidos do tenant, com lacunas documentadas. Turnos restritos ambíguos
devem falhar fechados; a implementação reconhece apenas parte dessas ambiguidades.
O uso externo de tokens reversíveis e sequenciais diverge da política original;
[ADR 002](docs/adr/002-no-pii-external-egress.md) e
[ADR 005](docs/adr/005-multi-tenant-platform-branch.md) registram essa divergência.

O desenho original de `main` propunha uma solução menor: SQLite, seções curadas
e orquestração explícita ([ADR 003](docs/adr/003-sqlite-and-curated-policy-retrieval.md),
[ADR 004](docs/adr/004-explicit-orchestration.md)). O
[ADR 001](docs/adr/001-hybrid-provider-neutral-architecture.md) registra a estratégia
original de provedores, ainda proposta. ADR 005 delimita a variante desta branch.
Veja o [processo e decisões](docs/development-process.md) e o [índice](docs/README.md).

## Exemplos de interação

As cinco conversas são **determinísticas, com fake model**, não transcrições de
um LLM real. Demonstram o pipeline e consultas ao snapshot local:

1. [Violões disponíveis até R$ 1.000](examples/conversations/01-catalogo-violoes-ate-1000.md).
2. [Preço do Takamine GD20](examples/conversations/02-preco-takamine-gd20.md).
3. [Regras de devolução](examples/conversations/03-politica-devolucao.md).
4. [Pedido com contato verificado](examples/conversations/04-status-pedido-verificado.md).
5. [Fora do escopo e endereço da loja](examples/conversations/05-fora-do-escopo-e-endereco.md).

O pedido verificado e a aplicação de regras de devolução cobrem situações não
triviais. Os artefatos de conversa não devem publicar identificadores de clientes.
Gere novamente os exemplos com o Postgres disponível:

```bash
uv run python -m scripts.demo_conversations
```

## Testes e avaliação

Use os provedores offline e mantenha testes live desligados:

```bash
APP_ENV=test LLM_PROVIDER=fake EMBEDDING_PROVIDER=hash RUN_OPENAI_LIVE_TESTS=0 uv run pytest -q
uv run ruff check .
uv run ruff format --check .
uv run mypy app
APP_ENV=test LLM_PROVIDER=fake EMBEDDING_PROVIDER=hash uv run python -m scripts.eval_rag --json-out /dev/null
```

Testes de integração usam bancos descartáveis em Postgres; se ele estiver
indisponível, podem ser pulados. Confira os skips antes de interpretar um sucesso.
A avaliação cria e remove seu próprio banco, com Empório e um tenant sintético
usado só para testar isolamento. `/dev/null` descarta o JSON; sem `--json-out`,
o resultado é salvo em `evals/results/latest.json`, ignorado pelo Git.

Há 23 casos e três gates obrigatórios: vazamento entre tenants = 0, violações de
privacidade = 0 e erros do harness = 0. Código de saída 1 indica falha nesses
gates; 2 indica problema de ambiente. As métricas de qualidade são informativas:
mesmo 100% em fake/hash não mede compreensão, tom, robustez a paráfrases ou LLM real.

O [estado de integração](docs/development-process.md#42-state-at-integration-2026-11-01)
separa os resultados aceitos e as verificações desta rodada. Os relatórios de
[avaliação inicial](evals/REPORT.md), [segurança R1](docs/reviews/security-review.md)
e [testes adversariais](tests/adversarial/FINDINGS.md) preservam snapshots históricos.
A [revisão de arquitetura R3](docs/reviews/architecture-review.md) documenta limites
que continuam abertos mesmo após os testes passarem.

Validação deste roteiro em 2026-11-01: infraestrutura, migração, geração de chave,
seed (duas vezes), servidor e curls executados com sucesso. O banco foi isolado
para preservar o ambiente existente; nenhuma configuração do repositório foi alterada.
Estado integrado após X1 (2026-11-01): `ruff` e `ruff format` limpos, mypy limpo em
63 arquivos, 603 testes e 123 subtests passando em duas execuções seguidas, e a
avaliação offline com todos os gates obrigatórios aprovados e 100% nas seis métricas.
A falha intermitente O-8 continua aberta. O gerador de conversas foi executado com
sucesso e produz os cinco arquivos de `examples/conversations/`.
Instalação das dependências em máquina vazia e modo OpenAI não foram exercitados.

## Limitações e próximos passos

- **Qualidade e grounding (A-2/A-4, O-4):** falta avaliação real, com perguntas
  reservadas, múltiplos turnos e fontes conflitantes. Citação ou nome de produto
  não prova consistência de estoque, prazo ou condição. Renderizaria fatos críticos
  localmente a partir de evidência tipada e acrescentaria testes de contradição.
- **Privacidade (A-3, O-9):** nomes desconhecidos em minúsculas e identificadores
  contextuais podem escapar das regras. Tokens permitem vínculo dentro da conversa.
  Restringiria a fronteira externa a entradas públicas/abstratas estruturadas e
  manteria texto ambíguo local; não afirmo anonimização nem cobertura universal.
- **Concorrência (A-5):** GET/SET do vault e commits do turno não são atômicos;
  requisições simultâneas ou retries podem perder estado ou duplicar ações.
  Implementaria serialização por conversa e idempotência antes de uso concorrente.
- **Escopo (A-6):** a plataforma excede a necessidade demonstrada pelo caso.
  Manteria os recursos extras opcionais, sem uma reescrita às vésperas da entrega.
- **RAG (A-7/A-8):** faltam gerações imutáveis de processamento, troca atômica de
  índices, calibração com embeddings reais e avaliação de condições/conflitos.
  PDFs escaneados não têm OCR; tabelas e layouts complexos têm suporte limitado.
  O manual tem contatos e regras de parcelamento ambíguos: não os reconcilio por suposição.
- **Código e escala (A-9/A-10):** o serviço de chat concentra responsabilidades;
  várias requisições carregam clientes/produtos inteiros do tenant. Refatoraria
  evidência/validação/renderização e mediria memória, custo e p95 antes de escalar
  além dos 50 clientes e 65 produtos do seed.
- **Autorização e operação (A-11/A-12):** filtros de tenant são uma convenção
  testada, sem RLS; falta vínculo de usuário final à sessão HTTP. Filas e WhatsApp
  não têm garantia durável de entrega. Priorizaria identidade, inbox/outbox,
  retries e credenciais por tenant; Redis no Compose não possui volume durável.
- **Retenção e observabilidade (A-13/A-14):** purge existe, mas atividade da
  conversa e vida útil de documentos não estão completas. Faltam métricas de
  uso/custo e configuração efetiva; métricas atuais são por processo.
- **Falsos positivos (O-8/O-10):** UUIDs aleatórios podem parecer telefone e
  disparar `privacy_guard`; negações corretas de promoção podem ser bloqueadas
  pela regra lexical. São falhas conservadoras conhecidas, ainda abertas.

## Uso de assistentes de código

Usei **Claude Code (Anthropic)**, **Cursor**, **Hermes** e **OpenAI Codex** em sessões de agentes,
com terminais paralelos gerenciados pelo **Orca** e ambientes Python locais para
executar código, testes e avaliação. Eu, `antonio-real`, defini o
objetivo e as restrições, escolhi a direção de plataforma (ADR 005), decidi o que
aceitar e aprovei cada commit. Todos os commits têm o responsável como autor;
essa autoria não significa que todo o código tenha sido escrito manualmente.

Claude Code coordenou o trabalho: dividiu as frentes com contratos congelados e
propriedade exclusiva por arquivo, escreveu especificações de tarefas, respondeu
às dúvidas dos workers, reexecutou todos os checks antes de aceitar cada entrega
e propôs os commits. As ferramentas identificadas por frente são:

| Frente | Ferramenta |
|---|---|
| B — infraestrutura | Orca, Hermes e Cursor |
| C — RAG | Claude Code e Cursor; a escolha do modelo de embeddings no PostgreSQL/pgvector ainda está em aberto |
| D — ferramentas de comércio | Claude Code e Cursor; canal previsto: WhatsApp |
| E — primeira implementação do agente | OpenAI Codex |
| F — API (validação do fluxo) | OpenAI Codex |
| R1 — revisão de segurança | OpenAI Codex |
| E2, E3 — correções do agente | Claude Code |
| F2, F3 — correções da plataforma | Claude Code |
| R2 — testes adversariais | Claude Code |
| G1 — avaliação | Claude Code |
| R3 — revisão de arquitetura | OpenAI Codex |
| X1 — conversas de exemplo, filtro de catálogo e validação de estoque | OpenAI Codex |
| X2 — este README e alinhamento documental | OpenAI Codex |

O fluxo foi: **contratos → frentes paralelas → revisão separada (R1) → testes
adversariais (R2) → avaliação com gates (G1) → correções pelos responsáveis
(E2/F2, E3/F3) → revisão separada de arquitetura (R3) → este README**.
As rodadas se sobrepuseram em alguns pontos; o histórico e o
[registro de desenvolvimento](docs/development-process.md) mostram os checkpoints.

Nem tudo funcionou de primeira. Limites de uso interromperam frentes, retomadas
a partir dos checkpoints. A correção de preços E2 introduziu uma regressão:
valores em documentos passaram a ser aceitos como preços de produtos. A suíte
adversarial identificou **FINDING-5**; E3 restringiu esses valores e manteve o
caso como regressão. Os testes e as revisões separadas foram usados para verificar
as entregas dos assistentes; a decisão de aceitar permaneceu humana.
