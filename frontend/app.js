// Endereço do backend (VPS Oracle Cloud, atrás do Caddy que já hospeda outro
// app no mesmo servidor — por isso o prefixo /quadro-api).
const API_BASE_URL = "https://147-15-107-15.sslip.io/quadro-api";

const STORAGE_KEY = "quadro:access_key";
const STORAGE_THEME = "quadro:theme";

// Um pouco acima do pior caso do backend (extração + download + conversão),
// pra dar tempo do servidor responder antes do navegador desistir sozinho.
const REQUEST_TIMEOUT_MS = 8 * 60 * 1000;

const form = document.getElementById("download-form");
const urlInput = document.getElementById("url-input");
const keyInput = document.getElementById("key-input");
const toggleKeyVisibilityBtn = document.getElementById("toggle-key-visibility");
const submitBtn = document.getElementById("submit-btn");
const progressArea = document.getElementById("progress-area");
const progressCaption = document.getElementById("progress-caption");
const successArea = document.getElementById("success-area");
const errorArea = document.getElementById("error-area");
const statusDot = document.getElementById("status-dot");
const statusLabel = document.getElementById("status-label");
const statusTime = document.getElementById("status-time");
const platformChip = document.getElementById("platform-chip");
const platformName = document.getElementById("platform-name");
const platformIcon = document.getElementById("platform-icon");
const resultTitle = document.getElementById("result-title");
const resultDetail = document.getElementById("result-detail");
const saveFileBtn = document.getElementById("save-file-btn");
const resetBtn = document.getElementById("reset-btn");
const retryBtn = document.getElementById("retry-btn");
const errorDetail = document.getElementById("error-detail");
const themeToggle = document.getElementById("theme-toggle");

let pendingBlob = null;
let pendingFilename = "video.mp4";
let elapsedTimer = null;
let progressMessageTimer = null;

class ApiError extends Error {}

// --- tema claro/escuro -------------------------------------------------

function applyTheme(theme) {
  document.documentElement.dataset.theme = theme;
  themeToggle.classList.toggle("is-light", theme === "light");
}

(function initTheme() {
  let saved = null;
  try {
    saved = localStorage.getItem(STORAGE_THEME);
  } catch (err) {
    /* localStorage indisponível (modo privado, etc.) — segue com o padrão */
  }
  applyTheme(saved === "light" ? "light" : "dark");
})();

themeToggle.addEventListener("click", () => {
  const next = document.documentElement.dataset.theme === "light" ? "dark" : "light";
  applyTheme(next);
  try {
    localStorage.setItem(STORAGE_THEME, next);
  } catch (err) {
    /* ignora se não houver localStorage disponível */
  }
});

// --- chave de acesso salva -----------------------------------------------

(function initSavedKey() {
  try {
    const saved = localStorage.getItem(STORAGE_KEY);
    if (saved) {
      keyInput.value = saved;
    }
  } catch (err) {
    /* ignora se não houver localStorage disponível */
  }
})();

toggleKeyVisibilityBtn.addEventListener("click", () => {
  keyInput.type = keyInput.type === "password" ? "text" : "password";
});

// --- detecção de plataforma ------------------------------------------------

const PLATFORM_LABELS = { youtube: "YouTube", facebook: "Facebook" };
const PLATFORM_ICON_SVG = {
  youtube:
    '<svg width="11" height="11" viewBox="0 0 24 24" fill="none"><path d="M9 7v10l8-5-8-5Z" fill="currentColor"/></svg>',
  facebook:
    '<svg width="11" height="11" viewBox="0 0 24 24" fill="none"><circle cx="12" cy="12" r="8" stroke="currentColor" stroke-width="1.6"/></svg>',
};

function detectPlatform(rawValue) {
  let parsed;
  try {
    parsed = new URL(rawValue.trim());
  } catch (err) {
    return null;
  }
  if (!/^https?:$/.test(parsed.protocol)) return null;

  let host = parsed.hostname.toLowerCase();
  if (host.startsWith("www.")) host = host.slice(4);
  if (host.startsWith("m.")) host = host.slice(2);

  if (host === "youtube.com" || host.endsWith(".youtube.com") || host === "youtu.be") return "youtube";
  if (host === "facebook.com" || host.endsWith(".facebook.com") || host === "fb.watch") return "facebook";
  return null;
}

urlInput.addEventListener("input", () => {
  const platform = detectPlatform(urlInput.value);
  if (platform) {
    platformIcon.innerHTML = PLATFORM_ICON_SVG[platform];
    platformName.textContent = PLATFORM_LABELS[platform];
    platformChip.hidden = false;
  } else {
    platformChip.hidden = true;
  }
});

// --- estados visuais ---------------------------------------------------

const STATUS_LABELS = {
  idle: "aguardando link",
  processing: "processando",
  success: "concluído",
  error: "erro",
};

function setState(state) {
  progressArea.hidden = state !== "processing";
  successArea.hidden = state !== "success";
  errorArea.hidden = state !== "error";
  submitBtn.hidden = state !== "idle";

  urlInput.disabled = state === "processing";
  keyInput.disabled = state === "processing";

  statusDot.className = "dot dot-" + state;
  statusLabel.textContent = STATUS_LABELS[state];

  if (state === "processing") {
    startElapsedTimer();
  } else {
    stopElapsedTimer();
    if (state !== "success") statusTime.textContent = "--:--:--";
  }
}

function startElapsedTimer() {
  const start = Date.now();
  statusTime.textContent = "00:00:00";
  elapsedTimer = setInterval(() => {
    const elapsed = Math.floor((Date.now() - start) / 1000);
    const h = String(Math.floor(elapsed / 3600)).padStart(2, "0");
    const m = String(Math.floor((elapsed % 3600) / 60)).padStart(2, "0");
    const s = String(elapsed % 60).padStart(2, "0");
    statusTime.textContent = `${h}:${m}:${s}`;
  }, 1000);
}

function stopElapsedTimer() {
  if (elapsedTimer) clearInterval(elapsedTimer);
  elapsedTimer = null;
}

const PROGRESS_MESSAGES = [
  "verificando o link",
  "extraindo o melhor formato disponível (mp4)",
  "baixando vídeo",
  "preparando arquivo",
];

function startProgressMessages() {
  let i = 0;
  progressCaption.textContent = PROGRESS_MESSAGES[0];
  progressMessageTimer = setInterval(() => {
    i = (i + 1) % PROGRESS_MESSAGES.length;
    progressCaption.textContent = PROGRESS_MESSAGES[i];
  }, 3500);
}

function stopProgressMessages() {
  if (progressMessageTimer) clearInterval(progressMessageTimer);
  progressMessageTimer = null;
}

// --- formatação ---------------------------------------------------------

function formatBytes(bytes) {
  if (!bytes) return "";
  const mb = bytes / (1024 * 1024);
  return mb >= 1 ? `${mb.toFixed(1)} MB` : `${Math.max(1, Math.round(bytes / 1024))} KB`;
}

function formatDuration(seconds) {
  const total = Number(seconds) || 0;
  const m = Math.floor(total / 60);
  const s = Math.floor(total % 60);
  return `${m}:${String(s).padStart(2, "0")}`;
}

function extractFilename(contentDisposition) {
  if (!contentDisposition) return "video.mp4";
  const match = contentDisposition.match(/filename="?([^";]+)"?/i);
  return match ? match[1] : "video.mp4";
}

// --- envio do formulário --------------------------------------------------

async function submitDownload() {
  const url = urlInput.value.trim();
  const accessKey = keyInput.value;

  if (!url) {
    urlInput.focus();
    return;
  }
  if (!accessKey) {
    keyInput.focus();
    return;
  }

  try {
    localStorage.setItem(STORAGE_KEY, accessKey);
  } catch (err) {
    /* ignora se não houver localStorage disponível */
  }

  setState("processing");
  startProgressMessages();

  // Sem isso, uma queda de conexão (ou o iOS pausando a aba em segundo
  // plano quando a tela bloqueia) deixa a página "carregando" pra sempre,
  // sem erro e sem jeito de tentar de novo.
  const controller = new AbortController();
  const abortTimer = setTimeout(() => controller.abort(), REQUEST_TIMEOUT_MS);

  try {
    const response = await fetch(`${API_BASE_URL}/api/download`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ url, access_key: accessKey }),
      signal: controller.signal,
    });

    if (!response.ok) {
      let message = "Ocorreu um erro ao baixar o vídeo.";
      try {
        const data = await response.json();
        if (data && data.message) message = data.message;
      } catch (err) {
        /* resposta sem corpo JSON — mantém mensagem genérica */
      }
      throw new ApiError(message);
    }

    const blob = await response.blob();
    pendingFilename = extractFilename(response.headers.get("Content-Disposition"));
    pendingBlob = blob;

    const title = response.headers.get("X-Video-Title") || pendingFilename.replace(/\.mp4$/i, "");
    const duration = response.headers.get("X-Video-Duration");

    resultTitle.textContent = title;
    const parts = ["mp4"];
    if (duration) parts.push(formatDuration(duration));
    parts.push(formatBytes(blob.size));
    resultDetail.textContent = parts.join(" · ");

    stopProgressMessages();
    setState("success");
  } catch (err) {
    stopProgressMessages();
    if (err instanceof ApiError) {
      errorDetail.textContent = err.message;
    } else if (err && err.name === "AbortError") {
      errorDetail.textContent =
        "A operação demorou demais e foi cancelada. Se o celular bloqueou a tela ou trocou de " +
        "aplicativo enquanto baixava, isso pode ter interrompido a conexão — tente novamente.";
    } else {
      errorDetail.textContent = "Não foi possível conectar ao servidor. Verifique sua conexão e tente novamente.";
    }
    setState("error");
  } finally {
    clearTimeout(abortTimer);
  }
}

form.addEventListener("submit", (event) => {
  event.preventDefault();
  submitDownload();
});

retryBtn.addEventListener("click", submitDownload);

saveFileBtn.addEventListener("click", () => {
  if (!pendingBlob) return;
  const objectUrl = URL.createObjectURL(pendingBlob);
  const link = document.createElement("a");
  link.href = objectUrl;
  link.download = pendingFilename;
  document.body.appendChild(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(objectUrl), 4000);
});

resetBtn.addEventListener("click", () => {
  pendingBlob = null;
  urlInput.value = "";
  platformChip.hidden = true;
  setState("idle");
  urlInput.focus();
});
