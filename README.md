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
Os testes verificam integração e controles exercitados, não naturalidade ou qualidade
de um LLM. Uma bateria manual com `gpt-5-mini` e `text-embedding-3-small` foi executada
em 2026-11-01 (ver [validação com modelo real](#validação-com-modelo-real)); não é uma
avaliação estatística.
O backend consulta um snapshot importado dos CSVs, não um ERP conectado em tempo real.

- [Executar com Docker (recomendado)](#executar-com-docker-recomendado)
- [Executar offline com Python/uv no host](#executar-offline)
- [Modelo real](#modo-com-modelo-real-opcional)
- [Decisões técnicas](#decisões-técnicas)
- [Exemplos](#exemplos-de-interação)
- [Testes e avaliação](#testes-e-avaliação)
- [Limitações](#limitações-e-próximos-passos)
- [Uso de assistentes](#uso-de-assistentes-de-código)

## Executar com Docker (recomendado)

### Pré-requisitos e início rápido

Instale apenas **Docker com Compose v2 ou superior**, com o daemon em execução.
Não é necessário instalar Python ou `uv` no host. Execute na raiz do repositório
com os dados fornecidos em `Desafio Técnico AI Engineer 2026/data/` (seis CSVs e
`políticas_da_loja.pdf`). O primeiro build precisa de internet para baixar imagens,
dependências e os encodings do tokenizer; o atendimento padrão usa fake/hash e
não chama a OpenAI. Os dados do caso ficam fora de todas as imagens e entram
somente por mount de leitura nos serviços opcionais `seed` e `test`.

Em macOS, Linux ou Windows com WSL/Bash:

```bash
./scripts/docker_up.sh --smoke
```

Sem `--smoke`, o script faz a mesma inicialização e verifica `/health`, sem enviar
uma mensagem. Ele cria `.env` a partir de `.env.example` somente se estiver ausente,
gera a chave administrativa dentro do container se estiver vazia, protege o arquivo
com modo 600 quando possível, executa migrações e seed, e inicia API e worker.
Pode ser executado novamente: preserva a configuração e reutiliza a chave existente.
O smoke exige HTTP 200 e `R$` na resposta à pergunta do caso.

A API estará em `http://127.0.0.1:8000`, com documentação em `/docs`. O script mostra
um exemplo de curl e o comando para carregar a chave sem exibi-la:

```bash
export SEED_API_KEY="$(grep '^SEED_API_KEY=' .env | tail -n 1 | cut -d= -f2-)"
curl --fail-with-body --silent --show-error http://127.0.0.1:8000/v1/chat/messages \
  -H "X-API-Key: $SEED_API_KEY" -H "Content-Type: application/json" \
  -d '{"message":"Quais opções de violões disponíveis custando até R$1000?"}'
```

Esse carregamento assume o formato sem aspas escrito pelo script. `SEED_API_KEY`
é administrativa: mantenha-a no terminal/servidor confiável e fora do Git.
Se a API não ficar saudável em 90 segundos, consulte `docker compose logs api`.

### Interface de teste (navegador)

Com a API e o seed prontos, abra `http://127.0.0.1:<API_PORT>/chat`
(por padrão, `http://127.0.0.1:8000/chat`) e cole a chave no campo de acesso.
O comando `export SEED_API_KEY=...` do início rápido carrega a chave de `.env`;
prefira a `SEED_CHAT_API_KEY`, se provisionada no seed, carregando-a da mesma forma.
Use a chave administrativa somente na máquina local. A interface mantém a chave
apenas em `sessionStorage`, na sessão da aba; apague o campo para removê-la.

Envie perguntas, continue a conversa ou clique em **Nova conversa**. Os detalhes
de cada resposta mostram ferramentas, evidência (`grounded`), fontes, latência e
eventual encaminhamento. Um turno é enviado por vez. Recarregar a página inicia
uma nova conversa; **Nova conversa** limpa a tela, sem apagar o histórico no servidor.

É uma interface de desenvolvimento e demonstração: segue `EXPOSE_API_DOCS` e
retorna 404 em produção, inclusive nos assets. Não é um widget de produção e não
oferece autenticação de usuário final. O provedor é o configurado na API; a página
não detecta se é fake/hash ou um modelo real.

### Equivalente manual

Para um ambiente novo, em Bash/WSL, execute os comandos abaixo. Se `.env` já
existir, preserve-o; se já houver `SEED_API_KEY`, pule sua geração e reutilize-a.

```bash
cp .env.example .env
docker compose build api
SEED_API_KEY="$(docker compose run --rm --no-deps -T api python -c 'from app.core.security import generate_api_key; print(generate_api_key()[0])')"
printf '\nSEED_API_KEY=%s\n' "$SEED_API_KEY" >> .env
chmod 600 .env
unset SEED_API_KEY
docker compose up -d db redis
docker compose --profile seed run --rm seed
docker compose up -d api worker
```

No Windows sem WSL, use PowerShell para a cópia e geração da chave (também apenas
em ambiente novo); depois execute os mesmos três comandos `docker compose` finais:

```powershell
Copy-Item .env.example .env
docker compose build api
$seedKey = docker compose run --rm --no-deps -T api python -c 'from app.core.security import generate_api_key; print(generate_api_key()[0])'
if ($LASTEXITCODE -ne 0) { throw 'Falha ao gerar a chave no container' }
Add-Content -Path .env -Encoding utf8 -Value "`nSEED_API_KEY=$seedKey"
Remove-Variable seedKey
```

O serviço `seed` aguarda a migração via `depends_on`; não é preciso instalar nem
executar Alembic no host. Para verificar a saúde sem curl instalado:

```bash
docker compose exec -T api python -c 'import urllib.request; print(urllib.request.urlopen("http://127.0.0.1:8000/health", timeout=5).status)'
```

### Portas, parada e reset

As portas padrão são API **8000**, Postgres **5434** e Redis **6380**, sempre
publicadas apenas em `127.0.0.1`. Configure `API_PORT`, `DB_PORT` e `REDIS_PORT`
no `.env` ou exporte-as no shell. Para uma stack isolada, por exemplo:

```bash
export COMPOSE_PROJECT_NAME=emporiod1
export API_PORT=8010 DB_PORT=5444 REDIS_PORT=6390
./scripts/docker_up.sh --smoke
```

No PowerShell, use `$env:API_PORT = '8010'` (e o mesmo para as outras variáveis),
ou edite `.env`. Dentro dos containers, os serviços continuam usando `db:5432`
e `redis:6379`; a alternativa com Python no host precisa ajustar suas URLs se
as portas publicadas forem alteradas. Mantenha o mesmo nome de projeto e as
mesmas variáveis em todos os comandos seguintes.

```bash
docker compose down       # para os serviços; preserva o volume do Postgres
docker compose down -v    # apaga TODOS os dados do projeto selecionado
```

Após o reset, execute o script novamente: **a mesma chave em `.env` funciona**,
pois o seed a provisiona no banco novo. Em um banco já inicializado, uma chave
diferente é rejeitada; não apague nem gere outra chave a cada execução.

### Modelo real dentro do Docker

Configure os provedores no `.env` conforme o [modo com modelo real](#modo-com-modelo-real-opcional):
`LLM_PROVIDER=openai` preserva o adaptador nativo; `LLM_PROVIDER=langchain` usa
`LANGCHAIN_CHAT_MODEL=provider:model`. Para embeddings, escolha `openai` ou `langchain`
e seu modelo de 1536 dimensões. Mantenha `OPENAI_TRACING_ENABLED=false`,
`LANGSMITH_TRACING=false` e `LANGCHAIN_TRACING_V2=false`.
Para Ollama no host, use `OLLAMA_BASE_URL=http://host.docker.internal:11434`
(o endereço `localhost` dentro do container aponta para o próprio container).

**Antes de trocar hash por embeddings reais, execute `docker compose down -v`**
na stack de demonstração e rode `./scripts/docker_up.sh` para migrar e indexar
um banco novo; isso apaga os dados existentes e o novo seed pode fazer chamadas cobradas.
Reexecutar o seed sobre os vetores antigos não garante reindexação. OpenAI, Anthropic
e Ollama reais não foram executados nesta validação.

### Testes e avaliação dentro do Docker

O target `test` instala as dependências de desenvolvimento e inclui os testes;
a imagem padrão de runtime não contém esses arquivos nem essas dependências.
Os comandos abaixo usam fake/hash, tracing desativado e testes live desligados,
mesmo quando `.env` está configurado para modelo real:

```bash
docker compose --profile test build test
docker compose --profile test run --rm test
docker compose --profile test run --rm test python -m scripts.eval_rag
```

Os testes e a avaliação criam e removem bancos descartáveis usando o usuário
local `emporio`, que tem permissão de `CREATE DATABASE`. O resultado JSON da
avaliação fica no container descartável; para preservá-lo no host, monte uma
pasta gravável em `/app/evals/results`. Confira skips e os três gates obrigatórios
antes de interpretar os resultados, conforme [Testes e avaliação](#testes-e-avaliação).

Validação em 2026-11-01: macOS/arm64, Bash 3.2.57, Docker Engine 29.7.2,
Compose v5.5.1 e Python 3.12.16 dentro da imagem Linux/arm64. A stack isolada
`emporiod1` (portas 8010/5444/6390) passou no script com smoke duas vezes,
preservando `.env` e a chave; a suíte no container passou com **603 testes e
123 subtests, sem skips**, e a avaliação passou nos três gates obrigatórios
(leakage 0, privacidade 0/23, erros 0). As seis métricas determinísticas ficaram
em 100%; isso não mede qualidade de LLM real. A imagem de runtime foi inspecionada:
sem `tests/`, dados do desafio ou pytest. Detalhes em
[processo de desenvolvimento](docs/development-process.md#43-docker-run-path-validation-2026-11-01).
Windows/WSL, host Linux e build `linux/amd64` não foram executados; ShellCheck
não estava instalado. O caminho manual PowerShell é fornecido sem validação local.

## Executar offline

Alternativa com **Python/uv no host**. Para executar tudo em containers, siga
[Executar com Docker](#executar-com-docker-recomendado).

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

Para testar no navegador, abra `http://127.0.0.1:8000/chat` e siga a seção
[Interface de teste (navegador)](#interface-de-teste-navegador).

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

Há três opções para chat: `fake` (determinístico), `openai` (adaptador nativo existente)
e `langchain` (adaptadores OpenAI, Anthropic ou Ollama sob o mesmo loop do Agents SDK).
Embeddings aceitam `hash`, `openai` e `langchain` (OpenAI ou Ollama; Anthropic não oferece
embeddings neste adaptador). A escolha não altera as regras de [privacidade](SECURITY.md):
`PrivacyGuardedModel` continua obrigatório, sem tools hospedadas, estado remoto ou tracing.
Veja a decisão e os limites no [ADR 006](docs/adr/006-langchain-model-layer.md).

Exemplo LangChain com OpenAI, em variáveis de ambiente ou `.env` ignorado pelo Git:

```dotenv
LLM_PROVIDER=langchain
LANGCHAIN_CHAT_MODEL=openai:gpt-5-mini
EMBEDDING_PROVIDER=langchain
LANGCHAIN_EMBEDDING_MODEL=openai:text-embedding-3-small
EMBEDDING_DIMENSIONS=1536
# OPENAI_API_KEY=<sua chave>
OPENAI_TRACING_ENABLED=false
LANGSMITH_TRACING=false
LANGCHAIN_TRACING_V2=false
```

Para Anthropic, troque `LANGCHAIN_CHAT_MODEL=anthropic:<modelo-disponível-na-sua-conta>`
e defina `ANTHROPIC_API_KEY`; mantenha um provedor separado de embeddings, por exemplo
OpenAI com sua chave. Para Ollama local, use `LANGCHAIN_CHAT_MODEL=ollama:<modelo-com-tools>`
e `OLLAMA_BASE_URL=http://localhost:11434`; no Docker, use
`http://host.docker.internal:11434`. Instale previamente o modelo no Ollama.
`LANGCHAIN_EMBEDDING_MODEL=ollama:<modelo-de-1536-dimensões>` exige vetores dessa dimensão;
modelos de outras dimensões exigem nova migração e reindexação, fora deste escopo.
Os nomes entre `<...>` são placeholders: a compatibilidade real dos modelos não foi testada.

O caminho nativo continua disponível com `LLM_PROVIDER=openai`, `EMBEDDING_PROVIDER=openai`,
`OPENAI_CHAT_MODEL=gpt-5-mini` e `OPENAI_EMBEDDING_MODEL=text-embedding-3-small`.
Os defaults são escolhas de implementação, sem benchmark de qualidade, custo ou latência.
O adaptador LangChain usa o modelo global `LANGCHAIN_CHAT_MODEL`; o campo nativo
`chat_model` do tenant não substitui essa escolha. Streaming LangChain não é implementado:
a API atual usa `Runner.run`, sem streaming. Falhas e timeouts mantêm o fallback/handoff local.

`MODEL_TIMEOUT_SECONDS=30` limita cada chamada ao modelo, inclusive nos adaptadores
LangChain; `TURN_TIMEOUT_SECONDS=75` limita o loop completo do agente, incluindo as
ferramentas. Assim, várias chamadas curtas podem completar um turno que ultrapasse 30s.
Ambos devem ser finitos e positivos: máximo de 300s por chamada e 600s por turno,
validados também em produção. `MAX_AGENT_TURNS=8` continua limitando as iterações;
`TOOL_TIMEOUT_SECONDS=5` continua sendo o limite individual das ferramentas.
Um dos dois timeouts do modelo/turno gera o fallback com motivo `tool_failure`.

**Não basta trocar as variáveis sobre um banco preenchido com hash embeddings.**
Os vetores podem ter a mesma dimensão e espaços incompatíveis. Para a API, use um
banco novo com migrações e seed já configurados para o provedor escolhido, ou reprocesse
todos os documentos antes de servir consultas. Reexecutar apenas o seed pode pular
documentos já concluídos; não há fingerprint de geração dos vetores. O harness `--live`
existente valida o caminho nativo OpenAI, cria banco descartável e gera chamadas cobradas;
não é uma avaliação comparativa dos adaptadores LangChain.

A primeira execução ao vivo de LangChain com `openai:gpt-5-mini` revelou que fechar
clientes HTTP compartilhados quebrava todos os turnos após o primeiro (D-27 / R8).
A correção cria clientes OpenAI próprios por instância e fecha apenas clientes próprios;
os clientes compartilhados de Anthropic permanecem abertos. Após a correção, a bateria
manual com `openai:gpt-5-mini` via LangChain teve 22 respostas úteis e 1 encaminhamento
correto em 27 turnos ([validação com modelo real](#validação-com-modelo-real)).
**Anthropic e Ollama permanecem sem execução real.** Os testes de regressão usam
transportes simulados e fake/hash; não medem qualidade, calibração de limiares nem retenção.
Antes de uso real, verifique os termos, retenção e controles de cada provedor; `store=False`
não substitui essa verificação. Qualquer flag LangSmith de tracing habilitada bloqueia a
inicialização em todos os ambientes.

## Decisões técnicas

| Escolha | Justificativa e alcance |
|---|---|
| Um agente com OpenAI Agents SDK | Sete ferramentas cobrem um único domínio; o loop limitado evita criar agentes especialistas sem necessidade. Um `GuardedModel` inspeciona cada payload antes do provedor, força `store=False` e bloqueia estado hospedado. |
| FastAPI como interface | HTTP permite reproduzir conversas com curl e exercitar autenticação, erros e isolamento; uma interface visual não é necessária para avaliar o agente. |
| OpenAI nativo, LangChain ou fake por configuração | LangChain adapta chat OpenAI/Anthropic/Ollama e embeddings OpenAI/Ollama; o loop segue no Agents SDK e a busca híbrida não muda. Factories explícitas evitam instalar LangGraph; LangSmith é apenas dependência transitiva, com tracing proibido. Regressões offline; a primeira execução real via LangChain revelou D-27; após a correção, 22 de 27 respostas úteis com `openai:gpt-5-mini`. Anthropic/Ollama não executados ao vivo. |
| Tools para fatos operacionais | Preço, estoque, promoção, pedido e cliente vêm de consultas tipadas ao `CommerceBackend`; o modelo não gera SQL nem usa RAG para inferir esses dados. |
| RAG somente documental | Políticas, FAQ e manuais precisam de trechos e referências; o seed contém o PDF da loja. Embeddings não substituem verificações de identidade. |
| Busca híbrida + RRF | pgvector/cosseno captura similaridade e full-text em português captura termos exatos; Reciprocal Rank Fusion combina posições, sem somar escalas incompatíveis. A superioridade sobre busca lexical não foi medida. |
| Suficiência antes de responder | Limiar de similaridade e cobertura lexical retêm fontes insuficientes; consultas filtram tenant, documento ativo e versão atual no SQL. É uma heurística de relevância, não prova de resposta completa. |
| Prompt versionado | `commerce-privacy-v5` combina persona configurável com regras obrigatórias; documentos chegam em `<untrusted_document>`, como dados não confiáveis. Guardrails e autorização continuam em código. |
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

Os comandos abaixo usam Python/uv no host. O roteiro de
[testes dentro do Docker](#testes-e-avaliação-dentro-do-docker) dispensa essas instalações.

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
O gerador de conversas foi executado com sucesso e produz os cinco arquivos de
`examples/conversations/`. Instalação das dependências em máquina vazia não foi exercitada.

### Validação com modelo real

Em 2026-11-01 a stack Docker foi executada com `gpt-5-mini` e `text-embedding-3-small`
contra uma bateria manual de 27 turnos (24 perguntas avulsas e uma conversa de 3 turnos).
Cada rodada de correção (R4–R8) foi seguida de uma nova execução completa; as rodadas
R5–R7 foram executadas duas vezes, porque o modelo não é determinístico.

| Execução | Respostas úteis (de 27) |
|---|---|
| Antes das correções | 8 |
| R4 | 15 |
| R5 (A / B) | 17 / 20 |
| R6 (A / B) | 23 / 17 |
| R7 (A / B) | 22 / 21 |
| R8, adaptador nativo | 19 |
| R8, LangChain (`openai:gpt-5-mini`) | 22 |

Em todas as execuções, "quero falar com um atendente" foi encaminhado corretamente.
A primeira execução revelou que o próprio filtro de privacidade bloqueava respostas
legítimas: IDs opacos gerados pelo provedor pareciam CEPs. Também revelou regras de
preço e estoque estritas demais para a redação de um LLM real, busca de catálogo
frágil a palavras extras e a singular/plural, tempo limite curto e, no caminho
LangChain, um cliente HTTP compartilhado fechado após o primeiro turno. As causas e
correções estão no [registro de desenvolvimento](docs/development-process.md) §4.5–4.9;
a tabela por pergunta está em [evals/live](evals/live/2026-11-01-gpt-5-mini.md).

O que ainda oscila entre execuções: a pergunta do caso cai para "sem informação" em parte
das tentativas, o pedido sem contato às vezes é encaminhado em vez de pedir o e-mail, e
o parcelamento sempre solicita confirmação humana, porque o manual traz regras divergentes
de parcela mínima. É um conjunto pequeno, usado para encontrar defeitos, não uma medida
de qualidade; Anthropic e Ollama não foram executados.

## Limitações e próximos passos

- **Qualidade e grounding (A-2/A-4, O-4):** a bateria real oscila entre 19 e 23
  respostas úteis em 27 turnos; falta uma avaliação com perguntas reservadas, mais
  conversas de vários turnos e fontes conflitantes. As regras de saída são lexicais:
  reduzem invenções, mas também rejeitam respostas corretas. Citação ou nome de produto
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
- **Falsos positivos (O-10):** negações corretas de promoção podem ser bloqueadas
  pela regra lexical; R5 corrigiu o caso específico em que “Custom 24” era lido
  como preço, sem resolver toda negação de promoção. A busca parcial pode retornar
  alternativas por termos em comum, sem garantir equivalência ao produto pedido.
  Tenants seed existentes precisam reaplicar a configuração pública com
  `scripts.create_tenant` para incorporar contatos/endereço faltantes. IDs opacos de transporte e UUIDs em campos próprios receberam
  tratamento específico em R4; números em texto livre continuam sujeitos ao scanner.

## Mensagem de boas-vindas

Configure `agent_settings.welcome_message` por tenant no banco (texto opcional, até
1.000 caracteres). O seed da Empório usa os horários do manual: segunda a sexta,
9h–18h; sábado, 9h–13h; domingos e feriados, fechado. Reexecutar o seed preenche
somente valores vazios e preserva o texto definido pelo operador.

No primeiro contato da conversa, uma saudação isolada recebe apenas esse texto,
sem chamada ao modelo. Se a mensagem já contém uma pergunta, o cliente recebe a
boas-vindas e a resposta normal, sem precisar repetir a pergunta. A API devolve
`welcome_message` antes de `reply` nesse segundo caso; WhatsApp e `/chat` exibem
mensagens separadas. A boas-vindas fica no histórico e não se repete. Tenants sem
texto configurado mantêm o comportamento anterior. Recusas, falhas e encaminhamentos
não recebem boas-vindas; os controles de segurança e privacidade continuam ativos.
Use apenas texto público da loja, sem dados pessoais ou placeholders de clientes.

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
| D1 — execução completa em Docker | OpenAI Codex |
| L1 — camada de modelos com LangChain | OpenAI Codex |
| U1 — interface de teste no navegador | OpenAI Codex |
| W1 — mensagem de boas-vindas | OpenAI Codex (implementação principal) e Cursor (revisão final, verificação e fechamento) |
| R4–R8 — correções após as execuções com modelo real | Claude Code executou as baterias e diagnosticou as causas; OpenAI Codex implementou as correções |

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
