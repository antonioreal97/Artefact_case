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

SAFE_FALLBACK = (
    "Não consegui confirmar essa informação com segurança. Posso ajudar com outra dúvida."
)
NO_INFORMATION = "Não encontrei informação confiável para responder à sua pergunta."
INJECTION_REFUSAL = (
    "Não posso revelar instruções internas ou alterar as regras de atendimento. "
    "Posso ajudar com informações da loja."
)
HANDOFF_ACK = "Entendo. Vou verificar como podemos ajudar com sua solicitação."
PRIVACY_NOTICE = (
    "Por segurança, não compartilhe por aqui dados pessoais como endereço, CPF ou documentos. "
    "Um atendente pode tratar disso com você por um canal seguro."
)
HIDDEN_VALUE = "[dado oculto]"

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
    r"\b(?:ja|foi|esta) (?:foi )?(?:encaminhad|registrad|solicitad)\w*"
)
_SUCCESS_IMPOSSIBLE = re.compile(
    r"\b(?:cancelei|reembolsei|estornei|alterei|reservei|aprovei|devolvi|troquei)\b"
)
_NO_INFO = re.compile(
    r"nao encontrei|nao tenho (?:essa |esta )?informac|nao sei (?:informar|responder)|"
    r"nao (?:consegui|foi possivel) (?:encontrar|localizar)"
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
    r"informe|gostaria de saber)\b"
)
_NUMBER = re.compile(
    r"(?<![\w.,])(\d{1,3}(?:\.\d{3})+(?:,\d{1,2})?|\d+(?:[.,]\d{1,2})?)(?![\w]|[.,]\d)"
)
_MONEY_BEFORE = re.compile(
    r"(?:r\$|brl|reais|\bpor|\bcust\w*|\bprec\w*|\bvalor\w*|\bsai(?: a| por)?|\bapenas|\bsomente)"
    r"\s*(?:de\s+|e\s+|:\s*|=\s*)?(?:r\$\s*)?$"
)
_MONEY_AFTER = re.compile(r"^\s*(?:reais|brl|r\$)")
_UNIT_AFTER = re.compile(
    r"^\s*(?:%|x\b|vezes|dias?\b|horas?\b|h\b|anos?\b|mes(?:es)?\b|semanas?\b|minutos?\b|"
    r"unidades?\b|itens?\b|pecas?\b|cm\b|mm\b|kg\b|g\b|m\b|polegadas?\b|watts?\b|w\b)"
)
_SENTENCE_SPLIT = re.compile(r"(?<!\d)[.!?;](?!\d)|\n")
# Price wording (folded text): such a sentence quotes a product price, not a policy value.
_PRICE_WORDING = re.compile(
    r"\bcust(?:a|am|ava|avam|ara|ando)\b|\bprecos?\b|\bsai(?:em|ra|rao)? (?:por|a)\b|"
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
    if re.fullmatch(r"\d{1,3}(?:\.\d{3})+(?:,\d{1,2})?", raw):
        raw = raw.replace(".", "").replace(",", ".")
    return Decimal(raw.replace(",", "."))


def money_amounts(text: str) -> list[Decimal]:
    """Monetary amounts with or without currency marker; 1.299 == 1.299,00 == 1299."""
    text = TOKEN_RE.sub(" ", text)
    folded = _fold(text)
    amounts: list[Decimal] = []
    for match in _NUMBER.finditer(folded):
        raw = match.group(1)
        before, after = folded[max(0, match.start() - 25) : match.start()], folded[match.end() :]
        if _UNIT_AFTER.match(after):
            continue
        has_cents = bool(re.search(r",\d{2}$", raw)) or bool(re.fullmatch(r"\d+\.\d{2}", raw))
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


def _price_violation(text: str, records: list[ToolCallRecord], documents: Iterable[str]) -> bool:
    """Product prices come only from this turn's catalog tools, never from documents.

    In a sentence that names a catalog product or uses price wording, every amount must
    be a list/promotional price returned this turn (of the named product, if any). Cited
    document amounts are accepted only as policy values (fees, thresholds) in sentences
    that name no product and use no price wording.
    """
    products = _products(records)
    all_prices = frozenset().union(*(p.prices for p in products))
    doc_amounts = {amount for doc in documents for amount in money_amounts(doc)}
    for sentence in _SENTENCE_SPLIT.split(text):
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


def output_violation(
    text: str,
    *,
    records: list[ToolCallRecord],
    labels: set[str],
    instructions: str,
    policy_text: str,
    documents: Iterable[str] = (),
) -> str | None:
    """Name of the first violated output rule, or None. Any violation → low_confidence."""
    if _price_violation(text, records, documents):
        return "price"
    if _commercial_claim_violation(text, records):
        return "commercial_claim"
    if not set(re.findall(r"\[(S\d+)\]", text)).issubset(labels):
        return "citation"
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


def has_factual_claim(text: str) -> bool:
    """Greetings, thanks and clarification questions make no factual claim."""
    for sentence in re.split(r"(?<=[.!?])\s+|\n", text.strip()):
        sentence = sentence.strip()
        if not sentence or sentence.endswith("?"):
            continue
        folded = _fold(sentence)
        has_digit = bool(re.search(r"\d", sentence))
        if _REQUEST.search(folded) and not has_digit:
            continue
        if has_digit or _CLAIM_WORDS.search(folded):
            return True
    return False


def discusses_sensitive_topic(text: str, *, cited: bool) -> bool:
    folded = _fold(text)
    return bool(_PERSONAL_HANDLING.search(folded)) or (
        not cited and bool(_SENSITIVE_ANSWER.search(folded))
    )
