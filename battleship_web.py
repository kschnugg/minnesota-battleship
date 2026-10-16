"""
Browser support for battleship_mn.py, used by index.html through Pyodide
(Python compiled to WebAssembly).

A web page can't pause Python while it waits for the player to type, so the
page replays the game instead: each time the player answers, the current
game is re-run from the same random seed with every answer given so far.
The game is deterministic for a given seed, so the replay reaches the same
point and stops at the next question. A full game replays in milliseconds.
"""

from __future__ import annotations

import os
import random
import tempfile
from typing import List, Optional

import battleship_mn as mn


class NeedInput(Exception):
    """Raised when a replay has used every answer and needs another one."""


class ReplayIO(mn.GameIO):
    """Feeds recorded answers to the game and collects everything it prints."""

    def __init__(self, answers: List[str]) -> None:
        self._answers = answers
        self._next = 0
        self._parts: List[str] = []
        self.prompt = ""

    def say(self, message: str = "") -> None:
        self._parts.append(f"{message}\n")

    def ask(self, prompt: str) -> str:
        if self._next >= len(self._answers):
            self.prompt = prompt
            raise NeedInput
        answer = self._answers[self._next]
        self._next += 1
        self._parts.append(f"{prompt}{answer}\n")  # echo, like a terminal
        return answer

    @property
    def text(self) -> str:
        return "".join(self._parts)


class ReplaySound:
    """Counts explosions instead of playing them; the page plays new ones."""

    def __init__(self) -> None:
        self.booms = 0

    def explosion(self) -> None:
        self.booms += 1


class WebSession:
    """
    One browser visit: a run of games with play-again prompts in between.

    After every call the page reads `text` (the whole screen so far),
    `prompt` (the question waiting for an answer), `booms` (explosions so
    far; the page plays a sound when this goes up) and `finished`.
    """

    def __init__(self, seed: Optional[int] = None) -> None:
        self._seeds = random.Random(seed)
        self._history = f"{mn.BANNER}\n"   # finished games, shown above the current one
        self._history_booms = 0
        self.text = ""
        self.prompt = ""
        self.booms = 0
        self.finished = False
        self._start_game()

    def answer(self, reply: str) -> None:
        """Record the player's reply to the current prompt and advance."""
        if self.finished:
            return
        self._answers.append(reply)
        self._replay()

    def _start_game(self) -> None:
        self._game_seed = self._seeds.randrange(2 ** 32)
        self._answers: List[str] = []
        self._replay()

    def _replay(self) -> None:
        io = ReplayIO(self._answers)
        sound = ReplaySound()
        game = mn.BattleshipGame(io, seed=self._game_seed, sound=sound)
        try:
            winner = game.play()
            game.announce_winner(winner)
            again = mn.ask_play_again(io)
        except NeedInput:
            self.text = self._history + io.text
            self.prompt = io.prompt
            self.booms = self._history_booms + sound.booms
            return

        self._history += io.text
        self._history_booms += sound.booms
        if again:
            self._start_game()
            return
        self._history += f"{mn.GOODBYE}\n"
        self.text = self._history
        self.prompt = ""
        self.booms = self._history_booms
        self.finished = True


def explosion_wav_bytes() -> bytes:
    """The same synthesized explosion the desktop game plays, as WAV bytes."""
    with tempfile.TemporaryDirectory() as folder:
        path = os.path.join(folder, "boom.wav")
        mn.write_explosion_wav(path, mn.SoundEffects.SAMPLE_RATE,
                               mn.SoundEffects.DURATION_SECONDS)
        with open(path, "rb") as wav_file:
            return wav_file.read()
