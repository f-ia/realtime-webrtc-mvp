// ===== CONFIG =====
const AUDIO_PLAYBACK_RATE = 1.15;

// ===== ESTADO GLOBAL =====
let phrases = [];
let currentIndex = 0;
let currentPhrase = null;

let mediaRecorder = null;
let audioChunks = [];
let isRecording = false;

let lastResult = null;
let attemptsByPhraseId = {};
let recordingSessionId = 0;

let currentAudioEl = null;

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

// ===== TEXTOS =====
const TUTOR = {
  idle: "Clique START para começar",
  loading: "Carregando frases...",
  ready: "Clique RECORD e leia a frase",
  recording: "Gravando... (Clique STOP quando terminar)",
  evaluating: "Avaliando...",
  completed: "Parabéns! Você completou tudo!",
  blockedAudio: "O navegador bloqueou o áudio automático. Clique no 🔊.",
};

// ===== DOM =====
const startBtn = document.getElementById("start");
const retryBtn = document.getElementById("retry");
const nextBtn = document.getElementById("next");
const stopBtn = document.getElementById("stop");
const playTargetBtn = document.getElementById("playTarget");

const statusBoxEl = document.getElementById("statusBox");
const statusEl = document.getElementById("status");

const logEl = document.getElementById("log");

const phraseTextEl = document.getElementById("phraseText");
const phraseTranslationEl = document.getElementById("phraseTranslation");

const exerciseNumberEl = document.getElementById("exerciseNumber");
const difficultyEl = document.getElementById("difficulty");

const progressBarEl = document.getElementById("progressBar");
const progressPercentEl = document.getElementById("progressPercent");

const resultCard = document.getElementById("resultCard");
const resultIcon = document.getElementById("resultIcon");
const resultTitle = document.getElementById("resultTitle");
const resultMsg = document.getElementById("resultMsg");
const resultMeta = document.getElementById("resultMeta");

// ===== UTILS =====
function setStatus(text, cls = "ready") {
  if (!statusEl) return;

  const nextClass = `status-text ${cls}`;
  const sameText = statusEl.textContent === text;
  const sameClass = statusEl.className === nextClass;
  if (sameText && sameClass) return;

  statusEl.textContent = text;
  statusEl.className = nextClass;
}

function log(msg) {
  if (!logEl) return;
  const div = document.createElement("div");
  div.textContent = `[${new Date().toLocaleTimeString()}] ${msg}`;
  logEl.appendChild(div);
  logEl.scrollTop = logEl.scrollHeight;
}

function updateProgress() {
  const total = phrases.length || 0;
  const percentage = total ? (currentIndex / total) * 100 : 0;
  progressBarEl.style.width = percentage + "%";
  progressPercentEl.textContent = Math.round(percentage) + "%";
}

function hideResultCard() {
  resultCard.classList.add("hidden");
  resultCard.classList.remove("ok", "warn");
}

function showResultCard(ok, title, msg, meta) {
  resultCard.classList.remove("hidden", "ok", "warn");
  resultCard.classList.add(ok ? "ok" : "warn");

  resultIcon.textContent = ok ? "✅" : "🟡";
  resultTitle.textContent = title;
  resultMsg.textContent = msg;
  resultMeta.textContent = meta || "";
}

function stopCurrentAudio() {
  try {
    if (!currentAudioEl) return;
    currentAudioEl.pause();
    currentAudioEl.currentTime = 0;
    currentAudioEl.src = "";
  } catch {}
  currentAudioEl = null;
}

function playBase64Audio(audioBase64, mime = "audio/mpeg") {
  stopCurrentAudio();

  const audio = new Audio(`data:${mime};base64,${audioBase64}`);
  currentAudioEl = audio;

  audio.playbackRate = AUDIO_PLAYBACK_RATE;

  try {
    audio.preservesPitch = true;
    audio.mozPreservesPitch = true;
    audio.webkitPreservesPitch = true;
  } catch {}

  audio.onended = () => {
    audio.src = "";
    if (currentAudioEl === audio) currentAudioEl = null;
  };

  return audio.play();
}

async function ttsSpeak(text) {
  const resp = await fetch("/tts", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ text }),
  });
  if (!resp.ok) return;

  const data = await resp.json();
  if (!data.audio_base64) return;

  try {
    await playBase64Audio(data.audio_base64, data.mime || "audio/mpeg");
  } catch {
    log(TUTOR.blockedAudio);
  }
}

function setVisible(el, visible) {
  if (!el) return;
  el.classList.toggle("hidden", !visible);
}

// ===== VERIFICAR SE É ÚLTIMA FRASE =====
function isLastPhrase() {
  return currentIndex === phrases.length - 1;
}

// ===== UI =====
function renderUI() {
  if (statusBoxEl) {
    const show = [states.LOADING, states.RECORDING, states.EVALUATING].includes(currentState);
    setVisible(statusBoxEl, show);
  }

  startBtn.disabled = true;
  retryBtn.disabled = true;
  nextBtn.disabled = true;
  stopBtn.disabled = true;

  setVisible(startBtn, true);
  setVisible(retryBtn, false);
  setVisible(nextBtn, false);
  setVisible(stopBtn, false);

  startBtn.classList.remove("is-recording");

  if (currentState === states.IDLE) {
    startBtn.disabled = false;
    startBtn.textContent = "🎙️ START";
    setStatus(TUTOR.idle, "ready");
    return;
  }

  if (currentState === states.LOADING) {
    startBtn.textContent = "Carregando...";
    setStatus(TUTOR.loading, "connecting");
    return;
  }

  if (currentState === states.SHOWING_PHRASE) {
    startBtn.disabled = false;
    startBtn.textContent = "🎙️ RECORD";
    setStatus(TUTOR.ready, "ready");
    return;
  }

  if (currentState === states.RECORDING) {
    setVisible(stopBtn, true);
    stopBtn.disabled = false;
    setStatus(TUTOR.recording, "listening");
    startBtn.classList.add("is-recording");
    return;
  }

  if (currentState === states.EVALUATING) {
    setStatus(TUTOR.evaluating, "connecting");
    return;
  }

  if (currentState === states.SHOWING_RESULT) {
    // Lógica: sucesso = mostra PRÓXIMO (ou nada na última frase)
    //         erro = mostra RETRY

    if (lastResult?.success) {
      // Acertou
      if (isLastPhrase()) {
        // Última frase: só mostra RETRY pra poder refazer se quiser
        // Mas se refizer e acertar, avança automaticamente
        setVisible(retryBtn, true);
        retryBtn.disabled = false;
        setVisible(nextBtn, false);
      } else {
        // Não é última: mostra PRÓXIMO
        setVisible(nextBtn, true);
        nextBtn.disabled = false;
        setVisible(retryBtn, false);
      }
    } else {
      // Errou: mostra RETRY
      setVisible(retryBtn, true);
      retryBtn.disabled = false;
      setVisible(nextBtn, false);
    }
    return;
  }

  if (currentState === states.COMPLETED) {
    startBtn.disabled = false;
    startBtn.textContent = "🔄 REINICIAR";
    setStatus(TUTOR.completed, "success");
  }
}

// ===== FLOW =====
async function loadPhrases() {
  currentState = states.LOADING;
  renderUI();

  try {
    const response = await fetch("/phrases");
    if (!response.ok) throw new Error("Falha ao carregar frases");

    const data = await response.json();
    phrases = data.phrases || [];
    log(`✅ ${phrases.length} frases carregadas`);

    currentIndex = 0;
    lastResult = null;
    attemptsByPhraseId = {};

    showPhrase();
  } catch (err) {
    log(`❌ ERRO ao carregar: ${err.message}`);
    currentState = states.IDLE;
    renderUI();
  }
}

function showPhrase() {
  if (currentIndex >= phrases.length) {
    showCompletion();
    return;
  }

  currentPhrase = phrases[currentIndex];
  attemptsByPhraseId[currentPhrase.id] = attemptsByPhraseId[currentPhrase.id] || 0;

  phraseTextEl.textContent = currentPhrase.text;
  phraseTranslationEl.textContent = currentPhrase.translation;

  exerciseNumberEl.textContent = `Exercício ${currentIndex + 1} de ${phrases.length}`;
  difficultyEl.textContent = currentPhrase.difficulty;

  updateProgress();
  hideResultCard();

  if (playTargetBtn) playTargetBtn.onclick = () => ttsSpeak(currentPhrase.text);

  lastResult = null;
  currentState = states.SHOWING_PHRASE;
  log(`📖 Frase ${currentIndex + 1}: "${currentPhrase.text}"`);
  renderUI();
}

async function startRecording() {
  if (currentState !== states.SHOWING_PHRASE) return;

  stopCurrentAudio();

  audioChunks = [];
  isRecording = true;
  const sessionId = ++recordingSessionId;

  try {
    const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    mediaRecorder = new MediaRecorder(stream);

    mediaRecorder.ondataavailable = (e) => audioChunks.push(e.data);

    mediaRecorder.onstop = async () => {
      if (sessionId !== recordingSessionId) return;

      currentState = states.EVALUATING;
      renderUI();
      log("⏹️ Gravação parada, enviando para servidor...");

      const mimeType = mediaRecorder.mimeType || "audio/webm";
      const audioBlob = new Blob(audioChunks, { type: mimeType });

      const reader = new FileReader();
      reader.onload = async (e) => {
        if (sessionId !== recordingSessionId) return;

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
          showResultCard(false, "Erro", "Não consegui avaliar agora. Tente novamente.", "");
          currentState = states.SHOWING_PHRASE;
          renderUI();
        }
      };

      reader.readAsDataURL(audioBlob);
    };

    mediaRecorder.start();
    currentState = states.RECORDING;
    renderUI();
    log("🎤 Gravação iniciada");
  } catch (err) {
    log(`❌ Erro no microfone: ${err.message}`);
    currentState = states.SHOWING_PHRASE;
    renderUI();
  }
}

function stopRecording() {
  if (!mediaRecorder || !isRecording) return;

  isRecording = false;
  try {
    mediaRecorder.stop();
    mediaRecorder.stream.getTracks().forEach((t) => t.stop());
  } catch {}
}

function showResult(result) {
  lastResult = result;
  currentState = states.SHOWING_RESULT;

  attemptsByPhraseId[currentPhrase.id] = (attemptsByPhraseId[currentPhrase.id] || 0) + 1;

  const meta = `Acurácia: ${result.score}% (meta: ${result.pass_score ?? 90}%) • Tentativas: ${
    attemptsByPhraseId[currentPhrase.id]
  }`;

  // ===== EXIBE RESULTADO NA TELA (SEM DUPLICATA) =====
  if (result.success) {
    // Só exibe o título "Muito bem!" e meta
    showResultCard(true, "Muito bem!", "", meta);
  } else {
    // Só exibe o título "Quase!" e meta (feedback IA vem no áudio)
    showResultCard(false, "Quase!", "", meta);
  }

  // ===== TOCA O ÁUDIO DO FEEDBACK (não da frase) =====
  // Prioridade: feedback_tts via TTS > audio_base64 do backend
  const feedbackTts = (result.feedback_tts || "").toString().trim();

  if (feedbackTts) {
    // Usa TTS pra gerar áudio do feedback (garantido sem duplicata)
    ttsSpeak(feedbackTts).catch(() => {
      log(TUTOR.blockedAudio);
    });
  } else if (result.audio_base64) {
    // Fallback: se o backend retornou áudio já pronto
    playBase64Audio(result.audio_base64, result.mime || "audio/mpeg").catch(() => {
      log(TUTOR.blockedAudio);
    });
  }

  // ===== Exibe tips (se houver) e feedback completo no log =====
  if (result.feedback) {
    log(`💬 ${result.feedback}`);
  }
  if (result.tips && Array.isArray(result.tips) && result.tips.length > 0) {
    const tipsText = result.tips.join(" • ");
    log(`💡 Dicas: ${tipsText}`);
  }

  // ===== AUTO-AVANÇAR NA ÚLTIMA FRASE SE ACERTOU =====
  if (result.success && isLastPhrase()) {
    // Acertou a última frase! Avança automaticamente após o áudio terminar
    const delayMs = 2500; // Aguarda 2.5s pra áudio sair completamente
    setTimeout(() => {
      currentIndex++;
      showPhrase();
    }, delayMs);
  }

  renderUI();
}

function showCompletion() {
  currentState = states.COMPLETED;
  hideResultCard();

  phraseTextEl.textContent = "🎉 Você completou o curso!";
  phraseTranslationEl.textContent = "Parabéns por sua dedicação!";

  updateProgress();
  renderUI();
  log("🎉 Muito bom! Você completou todos os exercícios!");
}

// ===== EVENTS =====
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
  if (currentState === states.COMPLETED) {
    logEl.innerHTML = "";
    currentIndex = 0;
    loadPhrases();
  }
};

retryBtn.onclick = () => {
  if (currentState !== states.SHOWING_RESULT) return;
  if (lastResult?.success) return;

  currentState = states.SHOWING_PHRASE;
  hideResultCard();
  renderUI();
  startRecording();
};

nextBtn.onclick = () => {
  if (currentState !== states.SHOWING_RESULT) return;
  if (!lastResult?.success) return;

  currentIndex++;
  showPhrase();
};

stopBtn.onclick = () => stopRecording();

window.addEventListener("load", () => {
  currentState = states.IDLE;
  hideResultCard();
  renderUI();
  log("Pronto. Clique START quando estiver pronto.");
});