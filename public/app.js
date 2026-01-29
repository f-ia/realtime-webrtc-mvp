// ===== ESTADO GLOBAL =====
let phrases = [];
let currentIndex = 0;      // índice da frase atual (só incrementa quando ACERTAR)
let currentPhrase = null;

let mediaRecorder = null;
let audioChunks = [];
let isRecording = false;

let lastResult = null;

const states = {
  IDLE: "idle",
  LOADING: "loading",
  SHOWING_PHRASE: "showing_phrase",
  RECORDING: "recording",
  EVALUATING: "evaluating",
  SHOWING_RESULT: "showing_result",
  COMPLETED: "completed",
};

let currentState = states.IDLE;

// ===== DOM ELEMENTS =====
const startBtn = document.getElementById("start");
const retryBtn = document.getElementById("retry");
const stopBtn = document.getElementById("stop");

const statusEl = document.getElementById("status");
const logEl = document.getElementById("log");

const phraseTextEl = document.getElementById("phraseText");
const phraseTranslationEl = document.getElementById("phraseTranslation");

const exerciseNumberEl = document.getElementById("exerciseNumber");
const difficultyEl = document.getElementById("difficulty");

const progressBarEl = document.getElementById("progressBar");
const progressPercentEl = document.getElementById("progressPercent");

const playTargetBtn = document.getElementById("playTarget");

// ===== UTILS =====
function setStatus(text, cls = "ready") {
  statusEl.textContent = text;
  statusEl.className = `status-text ${cls}`;
}

function log(msg) {
  const div = document.createElement("div");
  div.textContent = `[${new Date().toLocaleTimeString()}] ${msg}`;
  logEl.appendChild(div);
  logEl.scrollTop = logEl.scrollHeight;
}

function updateProgress() {
  // progresso = quantas frases já completou (currentIndex) / total
  const total = phrases.length || 0;
  const percentage = total ? (currentIndex / total) * 100 : 0;

  if (progressBarEl) progressBarEl.style.width = percentage + "%";
  if (progressPercentEl) progressPercentEl.textContent = Math.round(percentage) + "%";
}

function playBase64Audio(audioBase64, mime = "audio/mpeg") {
  const audio = new Audio(`data:${mime};base64,${audioBase64}`);
  audio.play();
}

async function ttsSpeak(text) {
  const resp = await fetch("/tts", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ text }),
  });
  if (!resp.ok) return;
  const data = await resp.json();
  if (data.audio_base64) playBase64Audio(data.audio_base64, data.mime || "audio/mpeg");
}

// ===== FLOW =====
async function loadPhrases() {
  currentState = states.LOADING;
  setStatus("Carregando frases...", "connecting");

  try {
    const response = await fetch("/phrases");
    if (!response.ok) throw new Error("Falha ao carregar frases");

    const data = await response.json();
    phrases = data.phrases || [];
    log(`✅ ${phrases.length} frases carregadas`);

    currentIndex = 0;
    lastResult = null;
    showPhrase();
  } catch (err) {
    log(`❌ ERRO ao carregar: ${err.message}`);
    setStatus(`Erro: ${err.message}`, "error");
    currentState = states.IDLE;
  }
}

function showPhrase() {
  if (currentIndex >= phrases.length) {
    showCompletion();
    return;
  }

  currentPhrase = phrases[currentIndex];
  currentState = states.SHOWING_PHRASE;
  lastResult = null;

  phraseTextEl.textContent = `"${currentPhrase.text}"`;
  phraseTranslationEl.textContent = currentPhrase.translation;

  exerciseNumberEl.textContent = `Exercício ${currentIndex + 1} de ${phrases.length}`;
  difficultyEl.textContent = currentPhrase.difficulty;

  updateProgress();

  setStatus("Clique RECORD e leia a frase", "ready");
  log(`📖 Frase ${currentIndex + 1}: "${currentPhrase.text}"`);

  startBtn.textContent = "🎙️ RECORD";
  startBtn.disabled = false;

  retryBtn.disabled = true;

  stopBtn.disabled = true;
  stopBtn.textContent = "⏹️ STOP";

  if (playTargetBtn) {
    playTargetBtn.onclick = () =>
      ttsSpeak(currentPhrase.text, "Read slowly, clearly, with good pronunciation.");
  }
}

async function startRecording() {
  if (![states.SHOWING_PHRASE, states.SHOWING_RESULT].includes(currentState)) return;

  audioChunks = [];
  isRecording = true;

  try {
    const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    mediaRecorder = new MediaRecorder(stream);

    mediaRecorder.ondataavailable = (e) => audioChunks.push(e.data);

    mediaRecorder.onstop = async () => {
      currentState = states.EVALUATING;
      setStatus("Avaliando...", "connecting");
      log("⏹️ Gravação parada, enviando para servidor...");

      const mimeType = mediaRecorder.mimeType || "audio/webm";
      const audioBlob = new Blob(audioChunks, { type: mimeType });

      const reader = new FileReader();
      reader.onload = async (e) => {
        const audioBase64 = e.target.result.split(",")[1];

        try {
          const response = await fetch("/evaluate", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
              phrase_id: currentPhrase.id,
              audio: audioBase64,
              expected: currentPhrase.text,
              mime_type: mimeType,
            }),
          });

          const result = await response.json();
          if (!response.ok) throw new Error(result.error || "Avaliação falhou");

          showResult(result);
        } catch (err) {
          log(`❌ ERRO: ${err.message}`);
          setStatus(`Erro: ${err.message}`, "error");
          currentState = states.SHOWING_PHRASE;
          startBtn.disabled = false;
          stopBtn.disabled = true;
        }
      };

      reader.readAsDataURL(audioBlob);
    };

    mediaRecorder.start();
    currentState = states.RECORDING;

    setStatus("Gravando... (Clique STOP quando terminar)", "listening");
    startBtn.disabled = true;

    retryBtn.disabled = true;

    stopBtn.disabled = false;
    log("🎤 Gravação iniciada");
  } catch (err) {
    log(`❌ Erro no microfone: ${err.message}`);
    setStatus("Microfone não disponível", "error");
    currentState = states.SHOWING_PHRASE;
    startBtn.disabled = false;
    stopBtn.disabled = true;
  }
}

function stopRecording() {
  if (mediaRecorder && isRecording) {
    isRecording = false;

    stopBtn.disabled = true;
    startBtn.disabled = true;

    try {
      mediaRecorder.stop();
      mediaRecorder.stream.getTracks().forEach((t) => t.stop());
    } catch {}
  }
}

function showResult(result) {
  currentState = states.SHOWING_RESULT;
  lastResult = result;

  const successMsg = result.success ? "✅ Mandou bem!" : "🟡 Quase! Vamos tentar de novo";
  setStatus(successMsg, result.success ? "success" : "warning");

  log(successMsg);
  log(`📝 Você disse: "${result.transcript}"`);
  log(`📊 Acurácia: ${result.score}%`);
  log(`💬 Feedback: ${result.feedback}`);

  // toca o áudio (feedback + frase alvo)
  if (result.audio_base64) {
    playBase64Audio(result.audio_base64, result.mime || "audio/mpeg");
  }

  // UI: feedback (mantém tradução e mostra feedback abaixo)
  phraseTranslationEl.innerHTML = `
    <div style="background:${result.success ? "#e8f5e9" : "#fff3e0"}; padding:15px; border-radius:8px; margin-top:10px;">
      <strong>${result.feedback}</strong><br>
      <small>Acurácia: ${result.score}%</small>
    </div>
  `;

  stopBtn.disabled = true;

  if (result.success) {
    // Só avança quando acertar
    startBtn.textContent = "📝 PRÓXIMO";
    startBtn.disabled = false;

    retryBtn.disabled = true;
  } else {
    // Não avança: retry até acertar
    startBtn.textContent = "🎙️ TENTAR DE NOVO";
    startBtn.disabled = false;

    retryBtn.disabled = false;
  }
}

function showCompletion() {
  currentState = states.COMPLETED;
  setStatus("🎉 Parabéns! Completou todos!", "success");
  log("🎉 Muito bom! Você completou todos os exercícios!");

  phraseTextEl.textContent = "🎉 Você completou o curso!";
  phraseTranslationEl.textContent = "Parabéns por sua dedicação!";

  startBtn.textContent = "🔄 COMEÇAR NOVAMENTE";
  startBtn.disabled = false;

  retryBtn.disabled = true;
  stopBtn.disabled = true;

  updateProgress();
}

// ===== EVENT LISTENERS =====
startBtn.onclick = () => {
  if (currentState === states.IDLE) {
    logEl.innerHTML = "";
    loadPhrases();
    return;
  }

  if (currentState === states.SHOWING_PHRASE) {
    startRecording();
    return;
  }

  if (currentState === states.SHOWING_RESULT) {
    if (lastResult?.success) {
      currentIndex++;       // avança só se acertou
      showPhrase();
    } else {
      startRecording();     // tenta de novo na mesma frase
    }
    return;
  }

  if (currentState === states.COMPLETED) {
    logEl.innerHTML = "";
    currentIndex = 0;
    loadPhrases();
  }
};

retryBtn.onclick = () => {
  // retry sem avançar
  log("🔁 Retry na mesma frase");
  showPhrase();
};

stopBtn.onclick = () => stopRecording();

// Initial
window.addEventListener("load", () => {
  setStatus("Clique START para começar", "ready");
  log("Pronto para praticar! Clique START quando estiver pronto.");
});
