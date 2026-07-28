const state = {
  activeLogFile: null,
  controller: null,
  sending: false,
  sessionId: createSessionId(),
};

const $ = (selector) => document.querySelector(selector);

const elements = {
  tabs: document.querySelectorAll(".tab"),
  chatScreen: $("#chatScreen"),
  logsScreen: $("#logsScreen"),
  emptyChat: $("#emptyChat"),
  statusText: $("#statusText"),
  messageList: $("#messageList"),
  chatForm: $("#chatForm"),
  messageInput: $("#messageInput"),
  sendButton: $("#sendButton"),
  traceToggle: $("#traceToggle"),
  newChatButton: $("#newChatButton"),
  refreshLogsButton: $("#refreshLogsButton"),
  logFileList: $("#logFileList"),
  logEntryList: $("#logEntryList"),
};

function createSessionId() {
  if (crypto.randomUUID) {
    return `web-${crypto.randomUUID()}`;
  }
  return `web-${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

function setStatus(text) {
  elements.statusText.textContent = text;
}

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function formatDate(value) {
  if (!value) return "";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString("vi-VN", {
    dateStyle: "short",
    timeStyle: "medium",
  });
}

async function api(path, options = {}) {
  const response = await fetch(path, {
    headers: {
      "Content-Type": "application/json",
      ...(options.headers || {}),
    },
    ...options,
  });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(payload.error || `HTTP ${response.status}`);
  }
  return payload;
}

function switchScreen(screenName) {
  elements.tabs.forEach((button) => {
    button.classList.toggle("is-active", button.dataset.screen === screenName);
  });
  elements.chatScreen.classList.toggle("is-active", screenName === "chat");
  elements.logsScreen.classList.toggle("is-active", screenName === "logs");
  if (screenName === "logs") {
    loadLogs();
  }
}

function hideEmptyChat() {
  elements.emptyChat.classList.add("is-hidden");
}

function resetChat() {
  if (state.controller) {
    state.controller.abort();
  }
  state.controller = null;
  state.sending = false;
  state.sessionId = createSessionId();
  elements.messageList.innerHTML = "";
  elements.emptyChat.classList.remove("is-hidden");
  elements.messageInput.value = "";
  elements.sendButton.disabled = false;
  setStatus("New chat");
  switchScreen("chat");
  elements.messageInput.focus();
}

function updateThoughtVisibility() {
  document.querySelectorAll(".thought-panel").forEach((panel) => {
    panel.classList.toggle("is-hidden", !elements.traceToggle.checked);
  });
}

function addMessage(role, text, detail = {}) {
  hideEmptyChat();
  const article = document.createElement("article");
  article.className = `message ${role}`;
  const roleLabel = role === "user" ? "Bạn" : role === "error" ? "Lỗi" : "Gift Advisor";
  const traceHtml =
    detail.trace && detail.trace.length
      ? `<div class="thought-panel ${elements.traceToggle.checked ? "" : "is-hidden"}">
          <p class="thought-title">Thought / tool trace</p>
          <pre>${escapeHtml(detail.trace.join("\n"))}</pre>
        </div>`
      : "";
  const meta =
    detail.iterations !== undefined
      ? `<p class="log-meta">Iterations ${detail.iterations} · Tool calls ${
          detail.tool_calls?.length || 0
        } · Guardrail ${detail.guardrail_triggered ? "ON" : "OK"}</p>`
      : "";
  article.innerHTML = `
    <div class="message-content">
      <p class="message-role">${roleLabel}</p>
      ${traceHtml}
      <p class="message-body">${escapeHtml(text)}</p>
      ${meta}
    </div>
  `;
  elements.messageList.appendChild(article);
  elements.messageList.scrollTop = elements.messageList.scrollHeight;
  return article;
}

function createStreamingAgentMessage() {
  hideEmptyChat();
  const article = document.createElement("article");
  article.className = "message agent streaming";
  article.innerHTML = `
    <div class="message-content">
      <p class="message-role">Gift Advisor</p>
      <div class="thought-panel ${elements.traceToggle.checked ? "" : "is-hidden"}">
        <p class="thought-title">Thought / tool trace</p>
        <div class="thought-lines"></div>
      </div>
      <p class="message-body"><span class="typing-dot">Đang suy nghĩ...</span></p>
      <p class="log-meta"></p>
    </div>
  `;
  elements.messageList.appendChild(article);
  elements.messageList.scrollTop = elements.messageList.scrollHeight;

  const body = article.querySelector(".message-body");
  const meta = article.querySelector(".log-meta");
  const thoughtLines = article.querySelector(".thought-lines");
  let answerText = "";

  return {
    appendAnswer(text) {
      answerText += text;
      body.textContent = answerText;
      elements.messageList.scrollTop = elements.messageList.scrollHeight;
    },
    appendThought(line, kind) {
      const item = document.createElement("div");
      item.className = `thought-line ${kind || "trace"}`;
      item.textContent = line;
      thoughtLines.appendChild(item);
      elements.messageList.scrollTop = elements.messageList.scrollHeight;
    },
    setError(message) {
      article.className = "message error";
      body.textContent = message;
    },
    finish(detail = {}) {
      article.classList.remove("streaming");
      if (!answerText && detail.answer) {
        body.textContent = detail.answer;
      }
      if (detail.iterations !== undefined) {
        meta.textContent = `Iterations ${detail.iterations} · Tool calls ${
          detail.tool_calls?.length || 0
        } · Guardrail ${detail.guardrail_triggered ? "ON" : "OK"}`;
      }
    },
  };
}

function parseSseEvent(rawEvent) {
  const lines = rawEvent.replaceAll("\r\n", "\n").split("\n");
  let event = "message";
  const data = [];
  for (const line of lines) {
    if (line.startsWith("event:")) {
      event = line.slice(6).trim();
    } else if (line.startsWith("data:")) {
      data.push(line.slice(5).trimStart());
    }
  }
  if (!data.length) {
    return { event, payload: {} };
  }
  return { event, payload: JSON.parse(data.join("\n")) };
}

function splitSseBuffer(buffer) {
  const crlfIndex = buffer.indexOf("\r\n\r\n");
  const lfIndex = buffer.indexOf("\n\n");
  if (crlfIndex === -1 && lfIndex === -1) {
    return null;
  }
  if (crlfIndex !== -1 && (lfIndex === -1 || crlfIndex < lfIndex)) {
    return { raw: buffer.slice(0, crlfIndex), rest: buffer.slice(crlfIndex + 4) };
  }
  return { raw: buffer.slice(0, lfIndex), rest: buffer.slice(lfIndex + 2) };
}

async function streamChatResponse(message, streamMessage, signal) {
  const response = await fetch("/api/chat/stream", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      message,
      session_id: state.sessionId,
      provider: "default",
    }),
    signal,
  });

  if (!response.ok) {
    const payload = await response.json().catch(() => ({}));
    throw new Error(payload.error || `HTTP ${response.status}`);
  }
  if (!response.body) {
    throw new Error("Trình duyệt không hỗ trợ streaming response.");
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder("utf-8");
  let buffer = "";

  while (true) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    let part = splitSseBuffer(buffer);
    while (part) {
      buffer = part.rest;
      handleStreamEvent(parseSseEvent(part.raw), streamMessage);
      part = splitSseBuffer(buffer);
    }
  }

  if (buffer.trim()) {
    handleStreamEvent(parseSseEvent(buffer), streamMessage);
  }
}

function handleStreamEvent({ event, payload }, streamMessage) {
  if (event === "status") {
    setStatus(payload.message || "Agent đang chạy...");
    return;
  }
  if (event === "thought") {
    streamMessage.appendThought(payload.line, payload.kind);
    return;
  }
  if (event === "answer_delta") {
    streamMessage.appendAnswer(payload.text || "");
    return;
  }
  if (event === "error") {
    streamMessage.setError(payload.message || "Có lỗi khi chạy agent.");
    setStatus("Có lỗi khi chạy agent");
    return;
  }
  if (event === "done") {
    streamMessage.finish(payload);
    if (payload.provider && payload.model) {
      setStatus(`${payload.provider} · ${payload.model}`);
    }
  }
}

async function sendMessage(message) {
  state.sending = true;
  elements.sendButton.disabled = true;
  state.controller = new AbortController();
  setStatus("Agent đang trả lời...");
  addMessage("user", message);
  const streamMessage = createStreamingAgentMessage();
  try {
    await streamChatResponse(message, streamMessage, state.controller.signal);
  } catch (error) {
    if (error.name !== "AbortError") {
      streamMessage.setError(error.message);
      setStatus("Có lỗi khi chạy agent");
    }
  } finally {
    state.sending = false;
    state.controller = null;
    elements.sendButton.disabled = false;
    elements.messageInput.focus();
  }
}

async function loadLogs() {
  setStatus("Đang tải logs...");
  try {
    const payload = await api("/api/logs");
    renderLogFiles(payload.files || []);
    if (!state.activeLogFile && payload.files?.length) {
      state.activeLogFile = payload.files[0].name;
    }
    if (state.activeLogFile) {
      await loadLogFile(state.activeLogFile);
    } else {
      elements.logEntryList.innerHTML = '<div class="empty-state">Chưa có log.</div>';
    }
    setStatus("Logs đã sẵn sàng");
  } catch (error) {
    elements.logFileList.innerHTML = "";
    elements.logEntryList.innerHTML = `<div class="empty-state">${escapeHtml(
      error.message,
    )}</div>`;
    setStatus("Không tải được logs");
  }
}

function renderLogFiles(files) {
  if (!files.length) {
    elements.logFileList.innerHTML = '<div class="empty-state">Chưa có log.</div>';
    return;
  }
  elements.logFileList.innerHTML = files
    .map(
      (file) => `
        <button class="log-file-button ${
          file.name === state.activeLogFile ? "is-active" : ""
        }" type="button" data-log-file="${escapeHtml(file.name)}">
          <span class="log-file-name">${escapeHtml(file.name)}</span>
          <span class="log-file-date">${formatDate(file.modified_at)}${
            file.current ? " · đang chạy" : ""
          }</span>
        </button>
      `,
    )
    .join("");
}

async function loadLogFile(fileName) {
  state.activeLogFile = fileName;
  document.querySelectorAll(".log-file-button").forEach((button) => {
    button.classList.toggle("is-active", button.dataset.logFile === fileName);
  });
  const payload = await api(`/api/log-file?name=${encodeURIComponent(fileName)}`);
  renderLogEntries(payload);
}

function renderLogEntries(payload) {
  const entries = payload.entries || [];
  if (!entries.length) {
    elements.logEntryList.innerHTML = '<div class="empty-state">File log đang trống.</div>';
    return;
  }
  const notice = payload.truncated
    ? '<div class="empty-state">Đang hiển thị 300 dòng mới nhất.</div>'
    : "";
  elements.logEntryList.innerHTML =
    notice +
    entries
      .slice()
      .reverse()
      .map((entry) => renderLogEntry(entry))
      .join("");
}

function renderLogEntry(entry) {
  const title = escapeHtml(entry.event || "event");
  const time = formatDate(entry.timestamp);
  const session = entry.session_id ? `<code>${escapeHtml(entry.session_id)}</code>` : "";
  const query = entry.query
    ? `<p><strong>Query:</strong> ${escapeHtml(entry.query)}</p>`
    : "";
  const answer = entry.answer
    ? `<p><strong>Answer:</strong> ${escapeHtml(entry.answer)}</p>`
    : "";
  const trace =
    entry.trace && entry.trace.length
      ? `<details>
          <summary>Thought / tool trace</summary>
          <pre>${escapeHtml(entry.trace.join("\n"))}</pre>
        </details>`
      : "";
  const raw = `<details>
      <summary>Raw JSON</summary>
      <pre>${escapeHtml(JSON.stringify(entry, null, 2))}</pre>
    </details>`;
  return `
    <article class="log-entry">
      <p class="log-meta">Line ${entry._line || "-"} · ${escapeHtml(time)}</p>
      <h3>${title} ${session}</h3>
      ${query}
      ${answer}
      ${trace}
      ${raw}
    </article>
  `;
}

async function loadInitialState() {
  try {
    const statePayload = await api("/api/state");
    const provider = statePayload.provider || {};
    setStatus(provider.error ? provider.error : `${provider.name} · ${provider.model}`);
  } catch (error) {
    setStatus(error.message);
  }
}

elements.tabs.forEach((button) => {
  button.addEventListener("click", () => switchScreen(button.dataset.screen));
});

document.querySelectorAll("[data-suggestion]").forEach((button) => {
  button.addEventListener("click", () => {
    elements.messageInput.value = button.dataset.suggestion;
    elements.chatForm.requestSubmit();
  });
});

elements.chatForm.addEventListener("submit", (event) => {
  event.preventDefault();
  if (state.sending) return;
  const message = elements.messageInput.value.trim();
  if (!message) return;
  elements.messageInput.value = "";
  sendMessage(message);
});

elements.messageInput.addEventListener("keydown", (event) => {
  if (event.key === "Enter" && !event.shiftKey) {
    event.preventDefault();
    elements.chatForm.requestSubmit();
  }
});

elements.newChatButton.addEventListener("click", resetChat);
elements.traceToggle.addEventListener("change", updateThoughtVisibility);
elements.refreshLogsButton.addEventListener("click", loadLogs);

elements.logFileList.addEventListener("click", (event) => {
  const button = event.target.closest("[data-log-file]");
  if (!button) return;
  loadLogFile(button.dataset.logFile);
});

loadInitialState();
