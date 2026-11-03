"""Deterministic checks before/after the model loop. No model-based safety decisions."""

from __future__ import annotations

import json
import re
from collections.abc import Iterable
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

from app.services.privacy import fold_text
from app.services.privacy_vault import TOKEN_RE
from app.tools.context import ToolCallRecord

SAFE_FALLBACK = "Não consegui confirmar isso com segurança agora."
NO_INFORMATION = "Não achei informação confiável pra te responder isso."
OUT_OF_SCOPE = (
    "Posso te ajudar só com assuntos da loja: produtos, preços, estoque, pedidos e "
    "nossas políticas."
)
OUT_OF_SCOPE_EN = (
    "I can only help with store topics: products, prices, stock, orders, and our policies."
)
INJECTION_REFUSAL = (
    "Não posso revelar instruções internas nem mudar as regras do atendimento. "
    "Se for sobre a loja, me conta o que você precisa."
)
HANDOFF_ACK = "Certo, vou ver com a equipe como a gente pode te ajudar."
PRIVACY_NOTICE = (
    "Por segurança, não envia por aqui dados pessoais como endereço, CPF ou documentos. "
    "Um atendente pode tratar disso com você por um canal seguro."
)
HIDDEN_VALUE = "[dado oculto]"

# Full-string grammar: punctuation/spacing are allowed, unknown words are not.
_GREETING_ONLY = re.compile(
    r"(?:oi|ola|bom dia|boa tarde|boa noite|e ai|tudo bem|tudo bom|como vai|"
    r"obrigad[oa]|valeu|tchau)"
    r"(?:[\s!.,?;:]+(?:oi|ola|bom dia|boa tarde|boa noite|e ai|tudo bem|tudo bom|"
    r"como vai|obrigad[oa]|valeu|tchau))*[\s!.,?;:]*"
)


def is_greeting_only(text: str) -> bool:
    """Conservatively recognize greetings/pleasantries, never discard a request."""
    return _GREETING_ONLY.fullmatch(" ".join(fold_text(text).split())) is not None


CATALOG_TOOLS = frozenset({"search_products", "get_product_details", "get_price"})
_PRICE_KEYS = frozenset({"price", "promotional_price", "list_price", "effective_price"})

# Anchored to the assistant itself: "instruções de montagem" or "as regras de troca" pass.
_REFUSAL = re.compile(
    r"system prompt|prompt (?:de|do) sistema|\bprompt\b|mensage(?:m|ns) (?:do|de) sistema|"
    r"developer message|mensagem do desenvolvedor|"
    r"\b(?:suas|tuas|seus|teus) (?:instrucoes|diretrizes|comandos|configurac\w*|regras internas)"
    r"(?! (?:de|do|da|para) )|"
    r"\b(?:instrucoes|regras|diretrizes|configurac\w*) (?:internas?|do sistema|ocultas?|secretas?)|"
    r"\bdiretrizes que (?:voce|vc|tu) (?:segue|seguem|recebeu)|"
    r"\bpalavra por palavra\b|"
    r"\b(?:ignor\w*|desconsider\w*|esquec\w*)\b.{0,40}"
    r"\b(?:instrucoes|regras|diretrizes|ordens) (?:anteriores|acima|que (?:te|lhe|voce) "
    r"(?:deram|passaram|recebeu))|"
    r"\b(?:ignore|disregard|forget|override|bypass)\b.{0,40}"
    r"\b(?:previous|prior|above|system|your) (?:instructions|prompt|rules|guidelines)|"
    r"\byour (?:system )?(?:instructions|prompt|guidelines|rules)\b|"
    r"\bjailbreak|\bmodo (?:desenvolvedor|developer|dan)\b|\bdan mode\b"
)
_SENSITIVE = re.compile(
    r"reclam\w*|\bprocon\b|estorn\w*|chargeback|reembols\w*|"
    r"devolv\w* (?:o |meu |o meu |minha |a minha )?(?:dinheiro|valor pago)|dinheiro de volta|"
    r"cobrad[oa]s? (?:duas vezes|2 vezes|em dobro|a mais|errado|indevidamente)|"
    r"cobra\w* (?:duplicada|indevida|errada|em dobro|a mais|duas vezes|2 vezes)|"
    r"cancel\w* (?:a |o |minha |meu |essa |esse |a minha |o meu )?(?:compra|pedido|encomenda)|"
    r"\bfraude|cartao (?:foi )?clonado|clonaram|"
    r"(?:abrir|entrar com|mover|vou abrir) (?:um )?processo|processo judicial|"
    r"processar (?:a loja|voces|a empresa)|\badvogad\w*|juizado|defeito grave|"
    r"pagamento.{0,30}(?:recusado|duplicado|negado|nao (?:foi )?(?:aprovado|reconhecido))|"
    r"\bexcecao|desconto especial|\brefund|complaint|payment.{0,30}(?:declined|duplicate)"
)
# Requires intent: "presente para outra pessoa" or the surname "Pessoa" pass.
_HUMAN = re.compile(
    r"falar com (?:um |uma |o |a |algum |alguma )?(?:atendente|pessoa|humano|ser humano|alguem|"
    r"agente|operador|gerente|supervisor|vendedor)\b|"
    r"atendimento humano|atendente humano|pessoa (?:real|de verdade)|"
    r"\b(?:quero|queria|preciso|gostaria|chama\w*|passa\w*|transfer\w*|me coloca\w*)\b.{0,25}"
    r"\b(?:atendente|humano)\b|"
    r"\b(?:talk|speak) (?:to|with) (?:a |an )?(?:human|person|agent|representative)\b"
)
# Answers handling a payment/refund/cancellation problem. Personal handling always counts;
# generic wording counts only outside a cited policy answer (policies mention "estorno").
_PERSONAL_HANDLING = re.compile(
    r"\bseu reembolso|\bseu estorno|(?:vou|irei|posso|vamos) (?:cancelar|reembolsar|estornar|"
    r"devolver o dinheiro)|cancelamento d[oa] (?:sua |seu )(?:compra|pedido)"
)
_SENSITIVE_ANSWER = re.compile(
    r"estorn\w*|chargeback|cobranca (?:duplicada|indevida|em dobro)|"
    r"cobrad[oa]s? (?:duas vezes|em dobro|indevidamente)|\bfraude|cartao clonado|\bprocon\b|"
    r"reembols\w*|cancelamento d[oa] (?:compra|pedido)"
)
_SUCCESS_HANDOFF = re.compile(
    r"\b(?:encaminhei|registrei|solicitei|transferi|abri (?:um|uma|o|a) "
    r"(?:chamado|solicitacao|ticket|protocolo|atendimento))\b|"
    r"\b(?:ja|foi|esta) (?:foi )?(?:encaminhad|registrad|solicitad)\w*|"
    # Canonical chat suffix and bounded variants ("pra"/"para", optional "te ajudar").
    r"passei (?:sua |a )?solicitac\w* (?:pra|para) (?:um |uma )?atendente"
    r"(?:\w*)?(?: te ajudar)?"
)
_SUCCESS_IMPOSSIBLE = re.compile(
    r"\b(?:cancelei|reembolsei|estornei|alterei|reservei|aprovei|devolvi|troquei)\b"
)
_NO_INFO = re.compile(
    r"nao (?:encontrei|achei)|nao tenho (?:essa |esta )?informac|nao sei (?:informar|responder)|"
    r"nao (?:consegui|foi possivel) (?:encontrar|localizar)|"
    r"i (?:couldn'?t|could not|can'?t|cannot) find|"
    r"(?:couldn'?t|could not|can'?t|cannot) find (?:the |any |our )?"
    r"(?:store'?s? |documented |exact )?(?:information|policy|details|terms)|"
    r"i (?:don'?t|do not) have (?:that |this |the |exact )?(?:information|details|terms)|"
    r"no (?:reliable |exact )?(?:information|details|policy) (?:found|available)|"
    r"i won'?t guess"
)
_CLAIM_WORDS = re.compile(
    r"r\$|\b(?:precos?|custa\w*|reais|brl|estoque|disponive(?:l|is)|indisponive(?:l|is)|"
    r"em falta|esgotad\w*|prazos?|garantia|politicas?|frete|entrega\w*|devoluc\w*|reembols\w*|"
    r"troca\w*|parcel\w*|descontos?|promoc\w*|rastrei\w*|enviad[oa]|entregue|status|situacao|"
    r"cancelad[oa]|aprovad[oa]|horarios?|aberto|aberta|fechad[oa]|funcionamos|aceitamos|"
    r"oferecemos|vendemos)\b"
)
_REQUEST = re.compile(
    r"\b(?:preciso d[oa]s?|pode(?:ria)? (?:me )?(?:informar|enviar|confirmar|passar|dizer)|"
    r"preciso confirmar|(?:nao )?envie|informe|me passe|gostaria de saber)\b"
)
# Offers describe a possible lookup, not its result. Do not exempt a neighboring
# assertion or a numeric value just because the sentence starts with an offer.
_LOOKUP_ACTION = (
    r"(?:buscar|ver|checar|consultar|filtrar|pesquisar|verificar|procurar|"
    r"explicar|ajudar|localizar|comparar)"
)
_OFFER_OR_SCOPE = re.compile(
    rf"^\s*(?:[-*•]\s*)?(?:(?:(?:eu )?(?:posso|podemos|vou|vamos)\s+)?"
    rf"{_LOOKUP_ACTION}\b|(?:quer|deseja) (?:que eu |que a gente )?"
    r"(?:busc|vej|chec|consult|filtr|pesquis|verifi|procur)\w*\b)|"
    r"\b(?:atuo|ajudo) (?:so |somente |apenas )?(?:sobre|com)\b|"
    rf"\b(?:posso|podemos|vou|vamos)\s+{_LOOKUP_ACTION}\b|"
    r"^assim que voce (?:passar|enviar|informar)\b"
)
_ASSERTED_RESULT = re.compile(
    r"\b(?:temos|ha|tem|existe[m]?|esta|estao|esgotou|encontrei|encontramos|custa\w*|"
    r"oferecemos|vendemos|aceitamos|garantimos|enviado|entregue|aprovado|cancelado)\b|"
    r"\b(?:frete|entrega|prazo|preco|estoque|produto|pedido)\s+(?:sera|foi)\b|"
    r"\be (?:gratis|gratuit[oa]|imediat[oa]|garantid[oa]|de graca)\b"
)


def _nonfactual_request(sentence: str) -> bool:
    """Bounded offers/scope/contact requests without an asserted result or value."""
    return (
        not re.search(r"(?<!\w)\d|r\$|%", sentence)
        and not _ASSERTED_RESULT.search(sentence)
        and bool(_OFFER_OR_SCOPE.search(sentence) or _REQUEST.search(sentence))
    )


_NUMBER = re.compile(
    r"(?<![\w.,])("
    r"\d{1,3}(?:,\d{3})+(?:\.\d{1,2})?"  # en-US thousands: 1,000 / 1,000.00
    r"|"
    r"\d{1,3}(?:\.\d{3})+(?:,\d{1,2})?"  # pt-BR thousands: 1.000 / 1.000,00
    r"|"
    r"\d+(?:[.,]\d{1,2})?"  # plain / cents: 599.90 / 599,90 / 1000
    r")(?![\w]|[.,]\d)"
)
_MONEY_BEFORE = re.compile(
    r"(?:r\$|brl|reais|\bpor|\bcust(?:a|am|ava|avam|ara|ando|s|ed|ing)?\b|"
    r"\bprec\w*|\bprices?\b|\bvalor\w*|\bsai(?: a| por)?|\bapenas|\bsomente)"
    r"\s*(?:de\s+|e\s+|:\s*|=\s*)?(?:r\$\s*)?$"
)
_MONEY_AFTER = re.compile(r"^\s*(?:reais|brl|r\$)")
_UNIT_AFTER = re.compile(
    r"^\s*(?:%|x\b|vezes|dias?\b|horas?\b|h\b|anos?\b|mes(?:es)?\b|semanas?\b|minutos?\b|"
    r"unidades?\b|itens?\b|pecas?\b|cm\b|mm\b|kg\b|g\b|m\b|polegadas?\b|watts?\b|w\b)"
)
_SENTENCE_SPLIT = re.compile(r"(?<!\d)[.!?;](?!\d)|\n")
# Keep question marks attached to their clause for stock assertions.
_STOCK_SENTENCE_SPLIT = re.compile(r"(?<!\d)[.!;](?!\d)|(?<=\?)\s*|\n")
_BUDGET_BEFORE = re.compile(
    r"(?:\bate|\bno maximo|\babaixo de|\borcamento(?: de)?|\blimite(?: de)?|"
    r"\bup to|\bunder|\bbelow|\bat most|\bless than|\bmaximum(?: of)?|"
    r"\bbudget(?: of)?)\s*(?:r\$\s*)?$"
)
# Price wording (folded text): such a sentence quotes a product price, not a policy value.
_PRICE_WORDING = re.compile(
    r"\bcust(?:a|am|ava|avam|ara|ando|s|ed|ing)?\b|\bprecos?\b|\bprices?\b|"
    r"\bsai(?:em|ra|rao)? (?:por|a)\b|"
    r"\bpor (?:apenas |so |somente )?r\$|\bde r\$ ?[\d.,]+ por\b"
)
_FREE_TERMS = re.compile(r"\bgratis\b|\bgratuit[oa]s?\b|\bde graca\b")
_DISCOUNT_TERMS = re.compile(r"\bdescontos?\b|%\s*off\b")
# Store-wide scope. A documented seasonal discount "em todo o catálogo" is a policy, but
# nothing in the whole catalog is ever free by policy.
_BLANKET_SCOPE = re.compile(
    r"\btod[oa]s? (?:os |as )?(?:produtos|itens|instrumentos|artigos|mercadorias)\b|"
    r"\b(?:a )?loja (?:toda|inteira)\b|\btoda a loja\b|\bqualquer (?:produto|item)\b|"
    r"\btudo (?:e|esta|sai|fica|sera|vai ser)\b"
)
_BLANKET_FREE_SCOPE = re.compile(r"\btodo o catalogo\b|\bcatalogo (?:todo|inteiro)\b")


def _fold(text: str) -> str:
    return fold_text(text)


@dataclass(frozen=True)
class InputDecision:
    reason: str | None = None
    refusal: bool = False


def check_input(text: str) -> InputDecision:
    folded = _fold(text)
    if _REFUSAL.search(folded):
        return InputDecision(refusal=True)
    if _SENSITIVE.search(folded):
        return InputDecision(reason="sensitive_topic")
    if _HUMAN.search(folded):
        return InputDecision(reason="customer_request")
    return InputDecision()


def _to_decimal(raw: str) -> Decimal:
    if re.fullmatch(r"\d{1,3}(?:,\d{3})+(?:\.\d{1,2})?", raw):
        raw = raw.replace(",", "")
    elif re.fullmatch(r"\d{1,3}(?:\.\d{3})+(?:,\d{1,2})?", raw):
        raw = raw.replace(".", "").replace(",", ".")
    else:
        raw = raw.replace(",", ".")
    return Decimal(raw)


def money_amounts(text: str) -> list[Decimal]:
    """Monetary amounts with or without currency marker; 1.299 == 1,299.00 == 1299."""
    text = TOKEN_RE.sub(" ", text)
    folded = _fold(text)
    amounts: list[Decimal] = []
    for match in _NUMBER.finditer(folded):
        raw = match.group(1)
        before, after = folded[max(0, match.start() - 25) : match.start()], folded[match.end() :]
        if _UNIT_AFTER.match(after):
            continue
        has_cents = bool(re.search(r"[.,]\d{2}$", raw))
        if has_cents or _MONEY_BEFORE.search(before) or _MONEY_AFTER.match(after):
            try:
                amounts.append(_to_decimal(raw))
            except InvalidOperation:
                continue
    return amounts


@dataclass(frozen=True)
class _Product:
    name: str  # folded
    prices: frozenset[Decimal]  # list and promotional prices
    promoted: bool  # the tool returned an active promotion


def _products(records: Iterable[ToolCallRecord]) -> list[_Product]:
    """Products with their list/promotional prices from this turn's catalog tools."""
    found: list[_Product] = []

    def prices_in(value: object, into: set[Decimal]) -> None:
        if isinstance(value, dict):
            for key, child in value.items():
                if key in _PRICE_KEYS and child is not None:
                    try:
                        into.add(Decimal(str(child)))
                    except InvalidOperation:
                        pass
                else:
                    prices_in(child, into)
        elif isinstance(value, list):
            for child in value:
                prices_in(child, into)

    def visit(value: object) -> None:
        if isinstance(value, dict):
            if isinstance(value.get("name"), str) and "price" in value:
                prices: set[Decimal] = set()
                prices_in(value, prices)
                found.append(
                    _Product(
                        _fold(str(value["name"])).strip(),
                        frozenset(prices),
                        bool(value.get("active_promotion")),
                    )
                )
                return
            for child in value.values():
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)

    for record in records:
        if record.status == "success" and record.tool_name in CATALOG_TOOLS:
            visit(record.result_summary.get("data"))
    return found


def _named(products: list[_Product], folded_sentence: str) -> list[_Product]:
    return [p for p in products if p.name and p.name in folded_sentence]


def _price_violation(
    text: str, records: list[ToolCallRecord], documents: Iterable[str], current_message: str = ""
) -> bool:
    """Product prices come only from this turn's catalog tools, never from documents.

    In a sentence that names a catalog product or uses price wording, every amount must
    be a list/promotional price returned this turn (of the named product, if any). Cited
    document amounts are accepted only as policy values (fees, thresholds) in sentences
    that name no product and use no price wording.
    """
    products = _products(records)
    all_prices = frozenset().union(*(p.prices for p in products))
    doc_amounts = {amount for doc in documents for amount in money_amounts(doc)}
    customer_amounts = set(money_amounts(current_message))
    for sentence in _SENTENCE_SPLIT.split(text):
        # A customer budget can be repeated as a filter/header, never as the price
        # of a named product or a sentence asserting what something costs.
        folded = _fold(sentence)
        sentence = folded
        for match in reversed(list(_NUMBER.finditer(folded))):
            prefix = folded[: match.start()]
            if (
                _to_decimal(match.group(1)) in customer_amounts
                and _BUDGET_BEFORE.search(prefix)
                and not _named(products, prefix)
                and not _PRICE_WORDING.search(prefix)
            ):
                sentence = sentence[: match.start()] + " " + sentence[match.end() :]
        amounts = money_amounts(sentence)
        if not amounts:
            continue
        folded = _fold(sentence)
        named = _named(products, folded)
        if named:
            allowed: set[Decimal] = set().union(*(p.prices for p in named))
        elif _PRICE_WORDING.search(folded):
            allowed = set(all_prices)
        else:
            allowed = set(all_prices) | doc_amounts
        if any(amount not in allowed for amount in amounts):
            return True
    return False


def _commercial_claim_violation(text: str, records: list[ToolCallRecord]) -> bool:
    """Free/discount claims for the whole store or a named product need catalog backing.

    A catalog result covers single products, so a store-wide claim ("todos os produtos
    são gratuitos") is never backed. A named product needs a promotion (discount) or a
    zero price (free) in this turn's tool data. Policy sentences that name no product
    ("5% de desconto no PIX", "frete grátis acima de R$ 500") pass.
    """
    products = _products(records)
    for sentence in _SENTENCE_SPLIT.split(text):
        folded = _fold(sentence)
        free = bool(_FREE_TERMS.search(folded))
        if not free and not _DISCOUNT_TERMS.search(folded):
            continue
        if _BLANKET_SCOPE.search(folded) or (free and _BLANKET_FREE_SCOPE.search(folded)):
            return True
        for product in _named(products, folded):
            backed = Decimal(0) in product.prices if free else product.promoted
            if not backed:
                return True
    return False


def _ngrams(text: str, n: int = 8) -> set[tuple[str, ...]]:
    words = re.findall(r"\w+", _fold(text))
    return {tuple(words[i : i + n]) for i in range(len(words) - n + 1)}


_STOCK_NEGATIVE = re.compile(
    r"\bindisponive(?:l|is)\b|\besgotad[oa]s?\b|\bem falta\b|\b(?:sem|fora de) estoque\b|"
    r"\bnao (?:esta |estao |ha |temos |tem )?"
    r"(?:disponive(?:l|is)(?: em estoque)?|(?:em )?estoque)\b"
)
_STOCK_POSITIVE = re.compile(r"\bdisponive(?:l|is)\b|\bem estoque\b")
_STOCK_QUANTITY = re.compile(
    r"\b(\d+(?:\.\d{3})*)\s*(?:unidades?|pecas?)\b|"
    r"\bestoque\s*(?:de|:|=)\s*(\d+(?:\.\d{3})*)\b"
)
# A stock word alone can be a topic or modifier, not an assertion. Link finite
# assertion verbs to stock predicates within the clause. Preserve terse product
# predicates ("Yamaha C40: disponível") as elliptical availability assertions.
_STOCK_ASSERTION = re.compile(
    r"\b(?:temos|tem|ha|existe[m]?|esta|estao|encontrei|encontramos)\b"
    r"[^,;.!?\n]{0,100}\b(?:estoque|disponive(?:l|is)|indisponive(?:l|is)|"
    r"esgotad[oa]s?|em falta)\b|\besgotou\b|"
    r"\bdisponive(?:l|is) para pronta entrega\b"
)
_STOCK_ELLIPSIS = re.compile(
    r"^\s*(?:[-*•]\s*)?(?:[\w -]*\b\w*\d\w*\s*:?\s*|[\w -]+:\s*)"
    r"(?:disponive(?:l|is)|indisponive(?:l|is)|esgotad[oa]s?|em estoque|em falta)\b"
)


@dataclass(frozen=True)
class _Stock:
    name: str
    available: bool
    quantity: int | None


def _stock_violation(
    text: str, records: list[ToolCallRecord], documents: Iterable[str] = ()
) -> bool:
    """Validate explicit Portuguese stock claims against this turn's public tools.

    Quantities require a stock_quantity field (search summaries alone cannot back them).
    Named clauses bind to that product; unnamed availability requires unanimous evidence.
    This deliberately bounded lexical check is not general entailment.
    """
    facts: dict[str, _Stock] = {}
    for record in records:
        if record.status != "success" or record.tool_name not in CATALOG_TOOLS | {"check_stock"}:
            continue
        data = record.result_summary.get("data")
        if isinstance(data, dict) and "items" in data:
            data = data["items"]
        for item in data if isinstance(data, list) else [data]:
            if not isinstance(item, dict):
                continue
            name, available = item.get("name"), item.get("available")
            if not isinstance(name, str) or not isinstance(available, bool):
                continue
            quantity = item.get("stock_quantity")
            key = _fold(name).strip()
            facts[key] = _Stock(key, available, quantity if type(quantity) is int else None)

    policy_sentences = {
        sentence.strip()
        for document in documents
        for sentence in _SENTENCE_SPLIT.split(_fold(document))
    }
    for sentence in _STOCK_SENTENCE_SPLIT.split(_fold(text)):
        if sentence.rstrip().endswith("?"):
            continue
        if _nonfactual_request(sentence):
            continue
        if not facts and not (
            _STOCK_ASSERTION.search(sentence)
            or _STOCK_QUANTITY.search(sentence)
            or _STOCK_ELLIPSIS.search(sentence)
        ):
            continue
        # Split at product mentions so a valid quantity for B cannot justify a claim for A.
        mentions = sorted(
            (match.start(), match.end(), fact)
            for fact in facts.values()
            if fact.name
            for match in re.finditer(re.escape(fact.name), sentence)
        )
        # A verbatim cited generic condition (e.g. "produtos esgotados durante a
        # promoção") describes a policy, not the current stock of a specific product.
        if (
            not mentions
            and not _STOCK_QUANTITY.search(sentence)
            and re.sub(r"\[s\d+\]", "", sentence).strip() in policy_sentences
            and re.search(r"\b(?:se|caso|durante|informar|sugerir|estejam|quando)\b", sentence)
        ):
            continue
        clauses: list[tuple[str, list[_Stock]]] = []
        if mentions:
            clauses.append((sentence[: mentions[0][0]], list(facts.values())))
            for index, (_, end, fact) in enumerate(mentions):
                stop = mentions[index + 1][0] if index + 1 < len(mentions) else len(sentence)
                clauses.append((sentence[end:stop], [fact]))
        else:
            clauses.append((sentence, list(facts.values())))
        for clause, candidates in clauses:
            if "?" in clause:
                continue
            negative = bool(_STOCK_NEGATIVE.search(clause) or re.search(r"\besgotou\b", clause))
            positive = bool(_STOCK_POSITIVE.search(_STOCK_NEGATIVE.sub("", clause)))
            quantities = [
                int((match.group(1) or match.group(2)).replace(".", ""))
                for match in _STOCK_QUANTITY.finditer(clause)
            ]
            if not (negative or positive or quantities):
                continue
            if not candidates or (quantities and len(candidates) != 1):
                return True
            if any(
                (positive and not fact.available) or (negative and fact.available)
                for fact in candidates
            ):
                return True
            if any(quantity != candidates[0].quantity for quantity in quantities):
                return True
    return False


def output_violation(
    text: str,
    *,
    records: list[ToolCallRecord],
    labels: set[str],
    instructions: str,
    policy_text: str,
    documents: Iterable[str] = (),
    current_message: str = "",
) -> str | None:
    """Name of the first violated output rule, or None. Any violation → low_confidence."""
    if _price_violation(text, records, documents, current_message):
        return "price"
    if _commercial_claim_violation(text, records):
        return "commercial_claim"
    if _stock_violation(text, records, documents):
        return "stock"
    if not set(re.findall(r"\[(S\d+)\]", text)).issubset(labels):
        return "citation"
    # This public, fixed reply is deliberately included in the policy. Only the
    # complete bounded refusal is exempt, never a refusal plus extra content.
    if is_out_of_scope_refusal(text):
        return None
    folded = _fold(text)
    if any(
        len(line.strip()) >= 45 and _fold(line.strip()) in folded
        for line in instructions.splitlines()
    ):
        return "prompt_leak"
    if _ngrams(text) & _ngrams(policy_text):
        return "prompt_leak"
    if re.search(r"politica obrigatoria|versao da politica:|<untrusted_document", folded):
        return "prompt_leak"
    if _SUCCESS_IMPOSSIBLE.search(folded):
        return "success_claim"
    if _SUCCESS_HANDOFF.search(folded) and not any(
        r.tool_name == "request_human_handoff" and r.status == "success" for r in records
    ):
        return "success_claim"
    return None


def verified_lookup_tokens(records: Iterable[ToolCallRecord]) -> set[str]:
    tokens: set[str] = set()
    for record in records:
        if record.tool_name == "lookup_order" and record.status == "success":
            data = record.result_summary.get("data")
            if isinstance(data, dict) and data.get("identity_verified"):
                tokens.update(m.group() for m in TOKEN_RE.finditer(json.dumps(data)))
    return tokens


def answer_uses_tool_facts(text: str, records: list[ToolCallRecord]) -> bool:
    """Tool grounding needs the answer to use a fact from a successful tool this turn."""
    if _stock_violation(text, records):
        return False
    folded = _fold(text)
    names = {product.name for product in _products(records)}
    for record in records:
        data = record.result_summary.get("data")
        if record.status == "success" and record.tool_name == "check_stock":
            if isinstance(data, dict) and isinstance(data.get("name"), str):
                names.add(_fold(data["name"]).strip())
    if any(name and name in folded for name in names):
        return True
    return any(token in text for token in verified_lookup_tokens(records))


def admits_no_information(text: str) -> bool:
    return bool(_NO_INFO.search(_fold(text)))


def is_out_of_scope_refusal(text: str) -> bool:
    """Recognize bounded scope-only replies; never exempt a neighboring factual claim."""
    normalized = " ".join(_fold(text).split())
    if normalized in {_fold(OUT_OF_SCOPE), _fold(OUT_OF_SCOPE_EN)}:
        return True
    return bool(
        re.fullmatch(
            r"(?:desculpe[, —-]* )?posso(?: te)? ajudar (?:apenas|somente|so) com "
            r"assuntos da loja"
            r"(?:[:.]|!)(?: (?:posso(?: te)? ajudar com )?(?:produtos, precos, estoque, "
            r"pedidos e (?:nossas )?politicas(?: da loja)?)[.!])?",
            normalized,
        )
        or re.fullmatch(
            # Colon/list form ("topics: products, … policies!") and two-sentence form.
            r"(?:sorry[, —-]* )?i can only help with store(?:-related)? topics?"
            r"(?:[:.]|!)(?: (?:i can help with )?(?:products, prices, stock, orders(?:, and|,) "
            r"(?:our |store )?policies)[.!])?",
            normalized,
        )
    )


def _covered_by_persona(sentence: str, persona: Iterable[str]) -> bool:
    """Cover configured phrases, allowing only greeting/identity glue around them.

    Match contiguous phrases, not a bag of persona words: a configured founding
    year must not authorize an unrelated delivery date or a discount of that value.
    """
    normalized = " ".join(re.findall(r"\w+", sentence))
    phrases: set[str] = set()
    for value in persona:
        for part in _SENTENCE_SPLIT.split(_fold(value)):
            words = re.findall(r"\w+", part)
            for size in range(2, len(words) + 1):
                phrases.update(" ".join(words[i : i + size]) for i in range(len(words) - size + 1))
            if len(words) == 1:
                phrases.add(words[0])
    matched = False
    for phrase in sorted(phrases, key=len, reverse=True):
        normalized, count = re.subn(r"(?<!\w)" + re.escape(phrase) + r"(?!\w)", " ", normalized)
        matched = matched or bool(count)
    glue = {
        "ola",
        "bem",
        "vindo",
        "vinda",
        "ao",
        "a",
        "o",
        "sua",
        "seu",
        "loja",
        "sou",
        "eu",
        "somos",
        "assistente",
        "do",
        "da",
        "de",
        "em",
        "e",
        "especializada",
        "especializado",
    }
    return matched and set(normalized.split()) <= glue


def has_factual_claim(text: str, *, persona: Iterable[str] = ()) -> bool:
    """Detect unsupported claims, excluding requests and configured introductions."""
    # Placeholder indices are vault references, never factual numeric values.
    text = TOKEN_RE.sub(" ", text)
    persona = tuple(persona)
    for sentence in _STOCK_SENTENCE_SPLIT.split(text.strip()):
        sentence = sentence.strip()
        if not sentence or sentence.endswith("?"):
            continue
        folded = _fold(sentence)
        if _nonfactual_request(folded) or _covered_by_persona(folded, persona):
            continue
        if re.search(r"\d", folded) or _CLAIM_WORDS.search(folded):
            return True
    return False


def discusses_sensitive_topic(text: str, *, cited: bool) -> bool:
    folded = _fold(text)
    return bool(_PERSONAL_HANDLING.search(folded)) or (
        not cited and bool(_SENSITIVE_ANSWER.search(folded))
    )
