// Runs battleship_mn.py in the browser with Pyodide (Python in WebAssembly).
// battleship_web.py replays the game on each answer; this file draws the
// screen, sends answers and plays the explosion when the boom count goes up.
"use strict";

const screen = document.getElementById("screen");
const form = document.getElementById("command");
const input = document.getElementById("answer");
const sendButton = document.getElementById("send");
const soundButton = document.getElementById("sound");
const newGameButton = document.getElementById("new-game");

let web = null;        // the battleship_web Python module
let session = null;    // current battleship_web.WebSession
let shownBooms = 0;
let wavBytes = null;
let audioContext = null;
let boomBuffer = null;
let boomPending = false; // a hit landed before the sound finished decoding
let soundOn = localStorage.getItem("mn-battleship-sound") !== "off";

function showSoundSetting() {
  soundButton.textContent = soundOn ? "Sound: on" : "Sound: off";
  soundButton.setAttribute("aria-pressed", String(soundOn));
}

// Browsers only allow audio after the player taps or types, so the audio
// context is created (and the boom decoded) on the first interaction.
function unlockAudio() {
  if (!audioContext) {
    const AudioContextClass = window.AudioContext || window.webkitAudioContext;
    if (!AudioContextClass) return;
    audioContext = new AudioContextClass();
  }
  if (audioContext.state === "suspended") audioContext.resume();
  if (wavBytes && !boomBuffer) {
    const copy = wavBytes.slice().buffer;
    wavBytes = null;
    audioContext.decodeAudioData(copy).then((buffer) => {
      boomBuffer = buffer;
      if (boomPending) playBoomSound();
    }, () => {});
  }
}

function playBoomSound() {
  boomPending = false;
  if (!soundOn || !audioContext) return;
  if (!boomBuffer) {
    boomPending = true; // played as soon as decoding finishes
    return;
  }
  const source = audioContext.createBufferSource();
  source.buffer = boomBuffer;
  source.connect(audioContext.destination);
  source.start();
}

function playBoom() {
  screen.classList.remove("boom");
  void screen.offsetWidth; // restart the shake animation
  screen.classList.add("boom");
  playBoomSound();
}

function render() {
  screen.textContent = session.text + session.prompt;
  screen.scrollTop = screen.scrollHeight;
  if (session.booms > shownBooms) playBoom();
  shownBooms = session.booms;
  const done = session.finished;
  input.disabled = done;
  sendButton.disabled = done;
  input.placeholder = done ? "Tap New game to play again" : "Type your answer";
  if (!done) input.focus({ preventScroll: true });
}

function newGame() {
  if (session) session.destroy();
  session = web.WebSession();
  shownBooms = 0;
  render();
}

function showError(error) {
  console.error(error);
  screen.textContent += `\n\nUff da, something went wrong: ${error.message || error}\nReload the page to try again.`;
}

async function boot() {
  const pyodide = await loadPyodide();
  for (const name of ["battleship_mn.py", "battleship_web.py"]) {
    const response = await fetch(name, { cache: "no-cache" });
    if (!response.ok) throw new Error(`could not load ${name} (${response.status})`);
    pyodide.FS.writeFile(name, await response.text());
  }
  pyodide.runPython("import os, sys; sys.path.insert(0, os.getcwd())");
  web = pyodide.pyimport("battleship_web");
  const wavProxy = web.explosion_wav_bytes();
  wavBytes = wavProxy.toJs();
  wavProxy.destroy();
  newGame();
}

form.addEventListener("submit", (event) => {
  event.preventDefault();
  unlockAudio();
  if (!session || session.finished) return;
  const reply = input.value;
  input.value = "";
  try {
    session.answer(reply);
    render();
  } catch (error) {
    showError(error);
  }
});

soundButton.addEventListener("click", () => {
  soundOn = !soundOn;
  localStorage.setItem("mn-battleship-sound", soundOn ? "on" : "off");
  showSoundSetting();
  unlockAudio();
});

newGameButton.addEventListener("click", () => {
  unlockAudio();
  if (web) newGame();
});

document.addEventListener("pointerdown", unlockAudio);
document.addEventListener("keydown", unlockAudio);

showSoundSetting();
boot().catch(showError);
