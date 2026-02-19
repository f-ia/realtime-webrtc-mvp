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
let isPlayingTips = false;

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
  playingTips: "Reproduzindo dicas...",
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
const phraseTranslationBox = document.getElementById("phraseTranslationBox");
const phraseTranslationEl = document.getElementById("phraseTranslation");

const exerciseNumberEl = document.getElementById("exerciseNumber");
const difficultyEl = document.getElementById("difficulty");

const progressBarEl = document.getElementById("progressBar");
const progressPercentEl = document.getElementById("progressPercent");

const resultCard = document.getElementById("resultCard");
const resultIcon = document.getElementById("resultIcon");
const resultTitle = document.getElementById("resultTitle");
const resultMsg = document.getElementById("resultMsg");
const resultTranscript = document.getElementById("resultTranscript");
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

function showResultCard(ok, title, msg, meta, transcript) {
  resultCard.classList.remove("hidden", "ok", "warn");
  resultCard.classList.add(ok ? "ok" : "warn");

  resultIcon.textContent = ok ? "✅" : "🟡";
  resultTitle.textContent = title;
  resultMsg.textContent = msg || "";
  resultMeta.textContent = meta || "";

  // Show what the user said (same as backend log) when available
  const transcriptText = (transcript && String(transcript).trim()) || "";
  if (resultTranscript) {
    resultTranscript.textContent = transcriptText ? `Você disse: ${transcriptText}` : "";
    resultTranscript.classList.toggle("hidden", !transcriptText);
  }
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

  return new Promise((resolve, reject) => {
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
      resolve();
    };

    audio.onerror = reject;

    audio.play().catch(reject);
  });
}

/** Fetches TTS audio for text (no play). Returns { audio_base64, mime } or null. */
async function fetchTtsAudio(text) {
  const resp = await fetch("/tts", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ text }),
  });
  if (!resp.ok) return null;
  const data = await resp.json();
  return data.audio_base64 ? { audio_base64: data.audio_base64, mime: data.mime || "audio/mpeg" } : null;
}

async function ttsSpeak(text) {
  const audio = await fetchTtsAudio(text);
  if (audio) await playBase64Audio(audio.audio_base64, audio.mime);
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
    const show =
      [states.LOADING, states.RECORDING, states.EVALUATING].includes(currentState) || isPlayingTips;
    setVisible(statusBoxEl, show);
    if (isPlayingTips) setStatus(TUTOR.playingTips, "listening");
  }

  const showExerciseHeader = ![states.IDLE, states.LOADING].includes(currentState);
  setVisible(difficultyEl, showExerciseHeader);
  setVisible(playTargetBtn, showExerciseHeader);
  setVisible(phraseTranslationBox, showExerciseHeader);

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
    if (playTargetBtn) playTargetBtn.disabled = isPlayingTips;

    if (lastResult?.success) {
      // Acertou
      if (isLastPhrase()) {
        // Última frase: só mostra RETRY pra poder refazer se quiser
        setVisible(retryBtn, true);
        retryBtn.disabled = isPlayingTips;
        setVisible(nextBtn, false);
      } else {
        // Não é última: mostra PRÓXIMO
        setVisible(nextBtn, true);
        nextBtn.disabled = false;
        setVisible(retryBtn, false);
      }
    } else {
      // Errou: mostra RETRY (disabled enquanto tips estão sendo lidas)
      setVisible(retryBtn, true);
      retryBtn.disabled = isPlayingTips;
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

  // Stop any previous recorder so only one recording uses audioChunks
  if (mediaRecorder && mediaRecorder.state !== "inactive") {
    try {
      mediaRecorder.stream.getTracks().forEach((t) => t.stop());
    } catch {}
    mediaRecorder = null;
  }
  audioChunks = [];
  isRecording = true;
  const sessionId = ++recordingSessionId;

  try {
    const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    mediaRecorder = new MediaRecorder(stream);

    mediaRecorder.ondataavailable = (e) => {
      if (e.data && e.data.size > 0) audioChunks.push(e.data);
    };

    mediaRecorder.onstop = async () => {
      if (sessionId !== recordingSessionId) return;

      // Capture chunks immediately so a new recording cannot overwrite them
      const chunks = audioChunks.slice();
      const mimeType = mediaRecorder.mimeType || "audio/webm";
      const audioBlob = new Blob(chunks, { type: mimeType });

      currentState = states.EVALUATING;
      renderUI();
      log("⏹️ Gravação parada, enviando para servidor...");

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
          if (!response.ok) throw new Error(result.error || result.message || "Avaliação falhou");

          showResult(result);
        } catch (err) {
          log(`❌ ERRO: ${err.message}`);
          showResultCard(false, "Erro", err.message || "Não consegui avaliar agora. Tente novamente.", "", null);
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

  let meta = `Acurácia: ${result.score}% (meta: ${result.pass_score ?? 90}%) • Tentativas: ${
    attemptsByPhraseId[currentPhrase.id]
  }`;
  if (result.used_fallback) {
    meta += " • Transcrição pode ter sido ajustada (fluxo reserva).";
  }

  // ===== EXIBE RESULTADO NA TELA (SEM DUPLICATA) =====
  if (result.success) {
    showResultCard(true, "Muito bem!", "", meta, result.transcript);
  } else {
    showResultCard(false, "Quase!", "", meta, result.transcript);
  }

  // Play feedback sequence
  playFeedbackSequence(result);

  // ===== Exibe tips (se houver) e feedback completo no log =====
  if (result.feedback) {
    log(`💬 ${result.feedback}`);
  }
  if (result.tips && Array.isArray(result.tips) && result.tips.length > 0) {
    const stripMarkers = (s) => (typeof s === "string" ? s.replace(/\[\[|\]\]/g, "") : s);
    const tipsText = result.tips.map(stripMarkers).join(" • ");
    log(`💡 Dicas: ${tipsText}`);
  }

  // ===== AUTO-AVANÇAR NA ÚLTIMA FRASE SE ACERTOU =====
  if (result.success && isLastPhrase()) {
    // Acertou a última frase! Avança automaticamente após o áudio terminar
    const delayMs = 2500; // Aguarda pra áudio sair completamente
    setTimeout(() => {
      currentIndex++;
      showPhrase();
    }, delayMs);
  }

  renderUI();
}

async function playFeedbackSequence(result) {
  const delay = (ms) => new Promise(r => setTimeout(r, ms));
  const pauseBetweenTipsMs = 25;

  if (!Array.isArray(result.tips) || result.tips.length === 0) {
    isPlayingTips = true;
    renderUI();
    try {
      await playBase64Audio(
        result.audio_base64,
        result.mime || "audio/mpeg"
      );
    } catch {
      log(TUTOR.blockedAudio);
    }
    isPlayingTips = false;
    renderUI();
    return;
  }

  isPlayingTips = true;
  renderUI();

  try {
    const tipTexts = result.tips.filter(Boolean);
    // Fetch first tip only so playback starts ASAP; fetch rest in background while first plays
    const firstPromise = fetchTtsAudio(tipTexts[0]);
    const restPromises = tipTexts.slice(1).map((text) => fetchTtsAudio(text));

    const first = await firstPromise;
    if (first) {
      try {
        await playBase64Audio(first.audio_base64, first.mime);
      } catch {
        log(TUTOR.blockedAudio);
      }
    }
    for (let i = 0; i < restPromises.length; i++) {
      await delay(pauseBetweenTipsMs);
      const a = await restPromises[i];
      if (!a) continue;
      try {
        await playBase64Audio(a.audio_base64, a.mime);
      } catch {
        log(TUTOR.blockedAudio);
      }
    }
  } finally {
    isPlayingTips = false;
    renderUI();
  }
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
  fetch("/api/config")
    .then((r) => r.json())
    .then((data) => {
      // Exibe o logBox somente no ambiente de desenvolvimento
      if (data.environment === "dev") {
        document.getElementById("logBox")?.classList.remove("hidden");
      }
    })
    .catch(() => {});
  log("Pronto. Clique START quando estiver pronto.");
});