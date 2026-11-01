"use strict";

const keyInput = document.getElementById("api-key");
const messageInput = document.getElementById("message");
const form = document.getElementById("chat-form");
const messages = document.getElementById("messages");
const status = document.getElementById("status");
const newConversation = document.getElementById("new-conversation");
const conversationState = document.getElementById("conversation-state");
const chips = [...document.querySelectorAll("#suggestions button")];
const storageKey = "emporio-chat-api-key";
let conversationId = null;
let waiting = false;

try {
  keyInput.value = sessionStorage.getItem(storageKey) || "";
} catch {
  document.getElementById("storage-note").textContent = "Armazenamento indisponível: a chave ficará apenas na memória desta página.";
}

function element(tag, text, className) {
  const node = document.createElement(tag);
  node.textContent = text;
  if (className) node.className = className;
  return node;
}

function resetConversation() {
  conversationId = null;
  messages.replaceChildren(element("p", "Como podemos ajudar? Escolha uma pergunta ou escreva sua mensagem."));
  messages.firstChild.id = "empty-state";
  conversationState.textContent = "Nova conversa";
  status.textContent = "";
  messageInput.value = "";
}

keyInput.addEventListener("input", () => {
  try {
    if (keyInput.value.trim()) sessionStorage.setItem(storageKey, keyInput.value.trim());
    else sessionStorage.removeItem(storageKey);
  } catch {
    document.getElementById("storage-note").textContent = "Armazenamento indisponível: a chave ficará apenas na memória desta página.";
  }
  // A different key may belong to another tenant: never reuse its conversation.
  resetConversation();
});

function setWaiting(value) {
  waiting = value;
  [messageInput, keyInput, newConversation, document.getElementById("send"), ...chips]
    .forEach((control) => { control.disabled = value; });
  status.textContent = value ? "O agente está respondendo…" : "";
}

function addMessage(text, kind) {
  document.getElementById("empty-state")?.remove();
  const article = element("article", "", `message ${kind}`);
  article.append(element("p", kind === "customer" ? "Você" : kind === "error" ? "Não foi possível concluir" : "Empório da Música", "speaker"));
  article.append(element("p", text, "message-text"));
  messages.append(article);
  messages.scrollTop = messages.scrollHeight;
  return article;
}

function addDetails(article, reply, elapsed) {
  if (reply.handoff) {
    article.append(element("p", `Encaminhamento solicitado: ${reply.handoff.reason}`, "handoff"));
  }
  const details = element("details", "");
  details.append(element("summary", "Detalhes da resposta"));
  details.append(element("p", `Ferramentas (tools_used): ${reply.tools_used?.join(", ") || "nenhuma"}`));
  details.append(element("p", `Com evidência (grounded): ${reply.grounded ? "sim" : "não"}`));
  details.append(element("p", `Encaminhamento (handoff): ${reply.handoff?.reason || "não"}`));
  const serverLatency = typeof reply.latency_ms === "number" && Number.isFinite(reply.latency_ms);
  details.append(element("p", `Latência: ${Math.round(serverLatency ? reply.latency_ms : elapsed)} ms (${serverLatency ? "servidor" : "medida no navegador"})`));
  details.append(element("p", "Fontes:"));
  const sources = element("ul", "");
  for (const source of reply.sources || []) {
    const parts = [source.label, source.title];
    if (source.section) parts.push(`Seção: ${source.section}`);
    if (source.page != null) parts.push(`Página: ${source.page}`);
    sources.append(element("li", parts.filter(Boolean).join(" · ")));
  }
  if (!sources.children.length) sources.append(element("li", "Nenhuma fonte documental retornada."));
  details.append(sources);
  article.append(details);
  messages.scrollTop = messages.scrollHeight;
}

function errorMessage(code) {
  if (code === 401) return "Chave inválida. Confira a chave de acesso e tente novamente.";
  if (code === 403) return "Esta chave não tem permissão de chat. Use uma chave com esse escopo.";
  if (code === 429) return "Limite de requisições atingido. Aguarde um pouco antes de tentar novamente.";
  if (code === 404) return "Conversa não encontrada. Clique em Nova conversa para recomeçar.";
  if (code >= 500) return "O serviço está indisponível no momento. Tente novamente em instantes.";
  return "Não foi possível enviar a mensagem. Confira o texto e tente novamente.";
}

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  if (waiting) return;
  const message = messageInput.value.trim();
  const apiKey = keyInput.value.trim();
  if (!apiKey) {
    status.textContent = "Cole uma chave de acesso antes de enviar.";
    keyInput.focus();
    return;
  }
  if (!message) { messageInput.focus(); return; }
  setWaiting(true);
  addMessage(message, "customer");
  messageInput.value = "";
  const started = performance.now();
  try {
    const body = { message };
    if (conversationId) body.conversation_id = conversationId;
    const response = await fetch("/v1/chat/messages", {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-API-Key": apiKey },
      body: JSON.stringify(body),
      cache: "no-store",
      redirect: "error",
    });
    if (!response.ok) {
      addMessage(errorMessage(response.status), "error");
      messageInput.value = message;
      return;
    }
    const reply = await response.json();
    if (typeof reply.reply !== "string" || typeof reply.conversation_id !== "string") {
      throw new Error("Unexpected response shape");
    }
    conversationId = reply.conversation_id;
    conversationState.textContent = "Conversa em andamento";
    if (typeof reply.welcome_message === "string" && reply.welcome_message) {
      addMessage(reply.welcome_message, "agent");
    }
    addDetails(addMessage(reply.reply, "agent"), reply, performance.now() - started);
  } catch {
    addMessage("Não foi possível confirmar a resposta. Verifique sua conexão e a API local. A mensagem pode ter sido processada; confira antes de reenviar.", "error");
    messageInput.value = message;
  } finally {
    setWaiting(false);
    messageInput.focus();
  }
});

messageInput.addEventListener("keydown", (event) => {
  if (event.key === "Enter" && !event.shiftKey && !event.isComposing) {
    event.preventDefault();
    form.requestSubmit();
  }
});

chips.forEach((chip) => chip.addEventListener("click", () => {
  if (waiting) return;
  messageInput.value = chip.textContent;
  form.requestSubmit();
}));

newConversation.addEventListener("click", () => {
  if (waiting) return;
  resetConversation();
  messageInput.focus();
});
