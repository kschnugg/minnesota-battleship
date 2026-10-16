#!/usr/bin/env python3
"""
Battleship: Land of 10,000 Lakes Edition
========================================

A single-player, text-based Battleship game with a Minnesota theme.
You command the North Star Fleet on Lake Superior against the
Cheesehead Navy, a computer opponent sailing over from Wisconsin.

Board legend:
    O  open water (empty ocean)
    S  a ship segment (only ever shown on your own board)
    X  a hit
    M  a miss

Coordinates are a row letter A-J plus a column number 1-10, e.g. "A5",
or two numbers "row col" from 1-10, e.g. "1 5" (the same cell as A5).

A synthesized explosion plays whenever you hit an enemy ship. Pass
--no-sound to play silently.

Run with:  python3 battleship_mn.py [--no-sound]
"""

from __future__ import annotations

import argparse
import atexit
import math
import os
import random
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import wave
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional, Set, Tuple

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

BOARD_SIZE = 10
ROW_LABELS = "ABCDEFGHIJ"

EMPTY = "O"
SHIP = "S"
HIT = "X"
MISS = "M"

# (ship class, size, Minnesota nickname). Sizes total 17 segments.
FLEET_SPEC: List[Tuple[str, int, str]] = [
    ("Carrier", 5, "Iron Range"),
    ("Battleship", 4, "Paul Bunyan"),
    ("Destroyer", 3, "North Star"),
    ("Submarine", 3, "Walleye"),
    ("Patrol Boat", 2, "Common Loon"),
]
TOTAL_SEGMENTS = sum(size for _, size, _ in FLEET_SPEC)

PLAYER_NAME = "North Star Fleet"
AI_NAME = "Cheesehead Navy"

# A coordinate is (row, col), both zero-based.
Coord = Tuple[int, int]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def coord_to_str(coord: Coord) -> str:
    """Convert zero-based (row, col) into a label such as 'A5' (columns 1-10)."""
    row, col = coord
    return f"{ROW_LABELS[row]}{col + 1}"


def parse_coordinate(text: str) -> Coord:
    """
    Parse user input into a (row, col) tuple.

    Accepted formats (case-insensitive, extra whitespace ignored):
        "A5", "a 5"   -> row letter A-J + column number 1-10
        "1 5", "1,5"  -> row number + column number, both 1-10

    Both examples above refer to the same cell: row A, column 5.

    Raises:
        ValueError: with a friendly message if the input is invalid.
    """
    cleaned = text.strip().upper()
    if not cleaned:
        raise ValueError("Ope, you didn't type anything. Try something like A5 or 1 5.")

    letter_match = re.fullmatch(r"([A-Z])\s*,?\s*(\d+)", cleaned)
    number_match = re.fullmatch(r"(\d+)\s*[,\s]\s*(\d+)", cleaned)

    if letter_match:
        letter, col_text = letter_match.groups()
        if letter not in ROW_LABELS:
            raise ValueError(f"Row '{letter}' isn't on the lake. Rows go A-{ROW_LABELS[-1]}.")
        row, col = ROW_LABELS.index(letter), int(col_text) - 1
    elif number_match:
        row, col = (int(part) - 1 for part in number_match.groups())
    else:
        raise ValueError("That's not a coordinate, don'tcha know. Use A5 or 1 5.")

    if not (0 <= row < BOARD_SIZE and 0 <= col < BOARD_SIZE):
        raise ValueError(
            f"That's off the map, up past the Boundary Waters. "
            f"Rows are A-{ROW_LABELS[-1]} (or 1-{BOARD_SIZE}) and columns are "
            f"1-{BOARD_SIZE}, e.g. A5 or 1 5."
        )
    return row, col


def parse_orientation(text: str) -> "Orientation":
    """
    Parse a direction into an Orientation.

    H = right, V = down, L = left, U = up. The words RIGHT/DOWN/LEFT/UP
    (and HORIZONTAL/VERTICAL, R, D) are accepted too.
    """
    choices = {
        "H": Orientation.RIGHT, "R": Orientation.RIGHT,
        "RIGHT": Orientation.RIGHT, "HORIZONTAL": Orientation.RIGHT,
        "V": Orientation.DOWN, "D": Orientation.DOWN,
        "DOWN": Orientation.DOWN, "VERTICAL": Orientation.DOWN,
        "L": Orientation.LEFT, "LEFT": Orientation.LEFT,
        "U": Orientation.UP, "UP": Orientation.UP,
    }
    orientation = choices.get(text.strip().upper())
    if orientation is None:
        raise ValueError(
            "Direction must be H (right), V (down), L (left) or U (up)."
        )
    return orientation


# ---------------------------------------------------------------------------
# Domain model
# ---------------------------------------------------------------------------

class Orientation(Enum):
    """Direction a ship extends from its starting (bow) coordinate."""
    RIGHT = "H"  # increasing column
    DOWN = "V"   # increasing row
    LEFT = "L"   # decreasing column
    UP = "U"     # decreasing row

    # Aliases: same values, so they refer to the members above.
    HORIZONTAL = "H"
    VERTICAL = "V"

    @property
    def delta(self) -> Coord:
        """(row step, column step) taken for each extra ship segment."""
        return {"H": (0, 1), "V": (1, 0), "L": (0, -1), "U": (-1, 0)}[self.value]


class ShotResult(Enum):
    """Outcome of firing at a cell."""
    MISS = "miss"
    HIT = "hit"
    SUNK = "sunk"


@dataclass
class Ship:
    """A single ship: its class, size, nickname, position and damage."""
    name: str
    size: int
    nickname: str
    coords: List[Coord] = field(default_factory=list)
    hits: Set[Coord] = field(default_factory=set)

    @property
    def is_placed(self) -> bool:
        return len(self.coords) == self.size

    @property
    def is_sunk(self) -> bool:
        return self.is_placed and len(self.hits) == self.size

    @property
    def display_name(self) -> str:
        return f"{self.name} \"{self.nickname}\""

    def register_hit(self, coord: Coord) -> None:
        """Record a hit on this ship (idempotent)."""
        if coord in self.coords:
            self.hits.add(coord)


class Board:
    """
    A 10x10 grid owned by one side. Tracks ship placement and incoming fire.

    The grid stores what is actually there (O/S/X/M). Rendering decides
    whether ships are revealed (own board) or hidden (tracking board).
    """

    def __init__(self, size: int = BOARD_SIZE) -> None:
        self.size = size
        self.grid: List[List[str]] = [[EMPTY] * size for _ in range(size)]
        self.ships: List[Ship] = []
        self._ship_at: Dict[Coord, Ship] = {}
        self.shots_received: Set[Coord] = set()

    # ----- placement -------------------------------------------------------

    def in_bounds(self, coord: Coord) -> bool:
        row, col = coord
        return 0 <= row < self.size and 0 <= col < self.size

    @staticmethod
    def ship_coords(start: Coord, size: int, orientation: Orientation) -> List[Coord]:
        """List the cells a ship of `size` would occupy from `start`."""
        row, col = start
        d_row, d_col = orientation.delta
        return [(row + i * d_row, col + i * d_col) for i in range(size)]

    def placement_error(self, size: int, start: Coord, orientation: Orientation) -> Optional[str]:
        """Return a reason the placement is invalid, or None if it is valid."""
        for coord in self.ship_coords(start, size, orientation):
            if not self.in_bounds(coord):
                return "That ship would run aground off the edge of the board."
            if coord in self._ship_at:
                other = self._ship_at[coord]
                return f"That overlaps your {other.name} at {coord_to_str(coord)}."
        return None

    def place_ship(self, ship: Ship, start: Coord, orientation: Orientation) -> None:
        """Place `ship` on the board. Raises ValueError if invalid."""
        error = self.placement_error(ship.size, start, orientation)
        if error:
            raise ValueError(error)
        ship.coords = self.ship_coords(start, ship.size, orientation)
        ship.hits.clear()
        for coord in ship.coords:
            self._ship_at[coord] = ship
            self.grid[coord[0]][coord[1]] = SHIP
        self.ships.append(ship)

    def place_ship_randomly(self, ship: Ship, rng: random.Random) -> None:
        """Place `ship` at a random valid position and orientation."""
        candidates = [
            ((row, col), orientation)
            # Right/down alone already cover every horizontal/vertical spot.
            for orientation in (Orientation.RIGHT, Orientation.DOWN)
            for row in range(self.size)
            for col in range(self.size)
            if self.placement_error(ship.size, (row, col), orientation) is None
        ]
        if not candidates:  # Cannot happen with the standard fleet on 10x10.
            raise RuntimeError(f"No room left to place the {ship.name}.")
        start, orientation = rng.choice(candidates)
        self.place_ship(ship, start, orientation)

    # ----- combat ----------------------------------------------------------

    def already_targeted(self, coord: Coord) -> bool:
        return coord in self.shots_received

    def receive_shot(self, coord: Coord) -> Tuple[ShotResult, Optional[Ship]]:
        """
        Resolve an incoming shot.

        Returns the result and the ship that was hit (None on a miss).
        Raises ValueError for out-of-bounds or repeated shots.
        """
        if not self.in_bounds(coord):
            raise ValueError("Shot is out of bounds.")
        if self.already_targeted(coord):
            raise ValueError(f"{coord_to_str(coord)} has already been targeted.")

        self.shots_received.add(coord)
        row, col = coord
        ship = self._ship_at.get(coord)
        if ship is None:
            self.grid[row][col] = MISS
            return ShotResult.MISS, None

        self.grid[row][col] = HIT
        ship.register_hit(coord)
        return (ShotResult.SUNK if ship.is_sunk else ShotResult.HIT), ship

    @property
    def segments_remaining(self) -> int:
        return sum(ship.size - len(ship.hits) for ship in self.ships)

    @property
    def all_sunk(self) -> bool:
        return bool(self.ships) and all(ship.is_sunk for ship in self.ships)

    # ----- rendering -------------------------------------------------------

    def render_rows(self, reveal_ships: bool) -> List[str]:
        """Return the board as text lines; hide 'S' unless `reveal_ships`."""
        lines = ["  " + " ".join(f"{c + 1:>2}" for c in range(self.size))]
        for r in range(self.size):
            cells = []
            for c in range(self.size):
                cell = self.grid[r][c]
                if cell == SHIP and not reveal_ships:
                    cell = EMPTY
                cells.append(cell)
            lines.append(f"{ROW_LABELS[r]} " + " ".join(f"{cell:>2}" for cell in cells))
        return lines


def build_fleet() -> List[Ship]:
    """Create a fresh, unplaced set of the five standard ships."""
    return [Ship(name, size, nickname) for name, size, nickname in FLEET_SPEC]


# ---------------------------------------------------------------------------
# Players
# ---------------------------------------------------------------------------

class Player:
    """Common state for any participant: a name, a board and a fleet."""

    def __init__(self, name: str) -> None:
        self.name = name
        self.board = Board()
        self.fleet = build_fleet()

    def has_lost(self) -> bool:
        return self.board.all_sunk


class HumanPlayer(Player):
    """The person at the keyboard. All I/O goes through `GameIO`."""

    def __init__(self, name: str, io: "GameIO") -> None:
        super().__init__(name)
        self.io = io

    def place_fleet(self, rng: random.Random) -> None:
        """Let the player place ships manually, or have them placed at random."""
        choice = self.io.ask(
            "Place your fleet yourself, or let the DNR do it for ya? "
            "[M]anual / [R]andom (default M): "
        ).strip().upper()
        if choice.startswith("R"):
            for ship in self.fleet:
                self.board.place_ship_randomly(ship, rng)
            self.io.say("\nThe DNR has launched your fleet. Here she is:")
            self.io.show_board(self.board, f"{self.name} (your waters)", reveal_ships=True)
            return

        for ship in self.fleet:
            self.io.show_board(self.board, f"{self.name} (your waters)", reveal_ships=True)
            self._place_one_ship(ship)
        self.io.show_board(self.board, f"{self.name} (your waters)", reveal_ships=True)
        self.io.say("Fleet's in the water. You betcha!")

    def _place_one_ship(self, ship: Ship) -> None:
        """Prompt until the given ship is placed successfully."""
        while True:
            self.io.say(f"\nPlace your {ship.display_name} (size {ship.size}).")
            try:
                start = parse_coordinate(self.io.ask("  Starting coordinate (e.g. A5 or 1 5): "))
                orientation = parse_orientation(
                    self.io.ask("  Direction - H (right), V (down), L (left) or U (up): ")
                )
                self.board.place_ship(ship, start, orientation)
                return
            except ValueError as err:
                self.io.say(f"  Uff da! {err}")

    def choose_target(self, enemy_board: Board) -> Coord:
        """Prompt until the player enters a valid, not-yet-targeted cell."""
        while True:
            try:
                coord = parse_coordinate(self.io.ask("Where do you want to fire, Captain? "))
            except ValueError as err:
                self.io.say(f"  {err}")
                continue
            if enemy_board.already_targeted(coord):
                self.io.say(f"  You already fired at {coord_to_str(coord)}. Pick a fresh spot, eh?")
                continue
            return coord


class AIPlayer(Player):
    """
    Computer opponent using a hunt/target strategy.

    * Hunt mode: fire at a random untargeted cell.
    * Target mode: after a hit, fire at adjacent cells (up, down, left,
      right) until the ship is sunk.
    * Line mode: once two or more hits sit side by side, the ship's
      direction is known, so fire only at the open ends of that line.
      If both ends are blocked (e.g. two ships lying next to each other),
      fall back to trying every neighbour of the unresolved hits.

    Once a ship sinks, its cells stop driving targeting; hits on other
    ships found along the way keep the AI in target mode.
    """

    DIRECTIONS: Tuple[Coord, ...] = ((-1, 0), (1, 0), (0, -1), (0, 1))

    def __init__(self, name: str, rng: random.Random) -> None:
        super().__init__(name)
        self.rng = rng
        self._untargeted: Set[Coord] = {
            (r, c) for r in range(BOARD_SIZE) for c in range(BOARD_SIZE)
        }
        self._open_hits: Set[Coord] = set()  # hits on ships not yet sunk

    def place_fleet(self) -> None:
        for ship in self.fleet:
            self.board.place_ship_randomly(ship, self.rng)

    def choose_target(self, enemy_board: Board) -> Coord:
        """Pick the next shot: line ends, then neighbours, else random."""
        candidates = self._line_end_targets() or self._neighbour_targets()
        pool = candidates or self._untargeted
        coord = self.rng.choice(sorted(pool))
        self._untargeted.discard(coord)
        return coord

    def record_result(self, coord: Coord, result: ShotResult, ship: Optional[Ship]) -> None:
        """Update targeting state with the result of the last shot."""
        if result is ShotResult.MISS:
            return
        self._open_hits.add(coord)
        if result is ShotResult.SUNK and ship is not None:
            # That ship is done; only hits on other ships remain interesting.
            self._open_hits -= set(ship.coords)

    def _line_end_targets(self) -> Set[Coord]:
        """
        Untargeted cells just past either end of a run of 2+ adjacent hits.

        For each hit that has another hit directly behind it (so the pair
        forms a line), walk forward past consecutive hits and take the
        first cell beyond the run.
        """
        targets: Set[Coord] = set()
        for row, col in self._open_hits:
            for d_row, d_col in self.DIRECTIONS:
                if (row - d_row, col - d_col) not in self._open_hits:
                    continue
                cell = (row + d_row, col + d_col)
                while cell in self._open_hits:
                    cell = (cell[0] + d_row, cell[1] + d_col)
                if cell in self._untargeted:
                    targets.add(cell)
        return targets

    def _neighbour_targets(self) -> Set[Coord]:
        """Untargeted cells directly up/down/left/right of any open hit."""
        return {
            (row + d_row, col + d_col)
            for row, col in self._open_hits
            for d_row, d_col in self.DIRECTIONS
            if (row + d_row, col + d_col) in self._untargeted
        }


# ---------------------------------------------------------------------------
# I/O
# ---------------------------------------------------------------------------

class GameIO:
    """Thin wrapper around console I/O so the game logic stays testable."""

    def say(self, message: str = "") -> None:
        print(message)

    def ask(self, prompt: str) -> str:
        return input(prompt)

    def show_board(self, board: Board, title: str, reveal_ships: bool) -> None:
        self.say(f"\n{title}")
        for line in board.render_rows(reveal_ships):
            self.say(line)

    def show_side_by_side(self, left: Board, left_title: str,
                          right: Board, right_title: str) -> None:
        """Print own board (ships shown) next to the tracking board (hidden)."""
        left_rows = left.render_rows(reveal_ships=True)
        right_rows = right.render_rows(reveal_ships=False)
        width = max(len(left_title), *(len(row) for row in left_rows)) + 6
        self.say()
        self.say(f"{left_title:<{width}}{right_title}")
        for l_row, r_row in zip(left_rows, right_rows):
            self.say(f"{l_row:<{width}}{r_row}")
        self.say(f"Legend: {EMPTY}=open water  {SHIP}=your ship  {HIT}=hit  {MISS}=miss")


# ---------------------------------------------------------------------------
# Sound
# ---------------------------------------------------------------------------

class SoundEffects:
    """
    Plays a loud, synthesized explosion when the player lands a hit.

    The sound is generated once with the standard library (no audio files
    or third-party packages) and played asynchronously with whatever the
    platform provides: winsound on Windows, afplay on macOS, and
    paplay/aplay/pw-play/ffplay on Linux. If nothing can play audio, it
    falls back to the terminal bell. Sound problems never stop the game.
    """

    SAMPLE_RATE = 22050
    DURATION_SECONDS = 1.0
    LINUX_PLAYERS = (
        ("paplay",),
        ("aplay", "-q"),
        ("pw-play",),
        ("ffplay", "-nodisp", "-autoexit", "-loglevel", "quiet"),
    )

    def __init__(self, enabled: bool = True) -> None:
        self.enabled = enabled
        self._wav_path: Optional[str] = None
        self._process: Optional[subprocess.Popen] = None
        atexit.register(self._finish_playing)

    def explosion(self) -> None:
        """Play the explosion (non-blocking). Silent if sound is disabled."""
        if not self.enabled:
            return
        try:
            if self._play_file(self._explosion_wav()):
                return
        except (OSError, RuntimeError, ValueError):
            pass
        print("\a", end="", flush=True)  # terminal bell fallback

    def _explosion_wav(self) -> str:
        """Create the explosion WAV in the temp folder (once) and return its path."""
        if self._wav_path and os.path.exists(self._wav_path):
            return self._wav_path
        handle, path = tempfile.mkstemp(prefix="battleship_mn_boom_", suffix=".wav")
        os.close(handle)
        write_explosion_wav(path, self.SAMPLE_RATE, self.DURATION_SECONDS)
        atexit.register(_remove_quietly, path)
        self._wav_path = path
        return path

    def _play_file(self, path: str) -> bool:
        """Start playing `path` in the background. Returns False if no player exists."""
        if sys.platform.startswith("win"):
            import winsound  # Windows-only standard library module
            winsound.PlaySound(path, winsound.SND_FILENAME | winsound.SND_ASYNC)
            return True
        players = (("afplay",),) if sys.platform == "darwin" else self.LINUX_PLAYERS
        for player in players:
            if shutil.which(player[0]):
                self._stop_previous()
                self._process = subprocess.Popen(
                    [*player, path],
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                return True
        return False

    def _stop_previous(self) -> None:
        """Cut off a boom that is still playing so sounds never pile up."""
        if self._process is not None and self._process.poll() is None:
            self._process.terminate()
            self._process.wait()

    def _finish_playing(self) -> None:
        """At exit, let the last boom finish (briefly), then clean it up."""
        if self._process is None or self._process.poll() is not None:
            return
        try:
            self._process.wait(timeout=self.DURATION_SECONDS + 0.5)
        except subprocess.TimeoutExpired:
            self._process.terminate()
            self._process.wait()


def write_explosion_wav(path: str, sample_rate: int, duration: float) -> None:
    """
    Write a mono 16-bit WAV of an explosion: a sharp burst of noise layered
    over a low rumble that sweeps from 110 Hz down to 35 Hz, fading out.
    """
    rng = random.Random(1858)  # fixed seed: same boom every time (MN statehood!)
    total = int(sample_rate * duration)
    frames = bytearray()
    phase = 0.0
    smoothed_noise = 0.0
    for i in range(total):
        t = i / sample_rate
        envelope = math.exp(-4.0 * t)
        attack = min(1.0, t / 0.005)  # 5 ms ramp avoids a click
        frequency = 35 + 75 * math.exp(-3.0 * t)
        phase += 2 * math.pi * frequency / sample_rate
        rumble = math.sin(phase)
        smoothed_noise += 0.35 * (rng.uniform(-1.0, 1.0) - smoothed_noise)
        sample = attack * envelope * (0.55 * rumble + 0.65 * smoothed_noise)
        sample = max(-1.0, min(1.0, sample * 1.6))  # push it loud, then clip
        frames += struct.pack("<h", int(sample * 32767))
    with wave.open(path, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(sample_rate)
        wav.writeframes(bytes(frames))


def _remove_quietly(path: str) -> None:
    try:
        os.remove(path)
    except OSError:
        pass


# ---------------------------------------------------------------------------
# Game controller
# ---------------------------------------------------------------------------

BANNER = r"""
==============================================================
     ___  ___  _____ _____ _    ___ ___ _  _ ___ ___
    | _ )/_\ |_   _|_   _| |  | __/ __| || |_ _| _ \
    | _ \/ _ \ | |   | | | |__| _|\__ \ __ || ||  _/
    |___/_/ \_\|_|   |_| |____|___|___/_||_|___|_|

            ~ Land of 10,000 Lakes Edition ~
==============================================================
 The Cheesehead Navy has crossed Lake Superior from Wisconsin,
 and they're eyein' the North Shore. Defend Minnesota, Captain!
"""

PLAYER_HIT_LINES = ["Hit! Oh, for cute!", "Hit! You betcha!", "Hit! That's a real doozy!"]
PLAYER_MISS_LINES = ["Miss! Just a splash and a confused walleye.",
                     "Miss! Nothin' but open water.",
                     "Miss! Ope, sorry, that one went wide."]
AI_HIT_LINES = ["Hit! Uff da, they got us!", "Hit! Oh, fer cryin' out loud!"]
AI_MISS_LINES = ["Miss! They splashed a loon. Rude.", "Miss! Not even close, eh?"]
GOODBYE = "\nThanks for playin'! Drive safe, watch for deer. Bye now!"


class BattleshipGame:
    """Runs a single game from fleet placement to the final announcement."""

    def __init__(self, io: Optional[GameIO] = None, seed: Optional[int] = None,
                 sound: Optional[SoundEffects] = None) -> None:
        self.io = io or GameIO()
        # Silent unless a SoundEffects is passed in (main() enables it).
        self.sound = sound or SoundEffects(enabled=False)
        self.rng = random.Random(seed)
        self.human = HumanPlayer(PLAYER_NAME, self.io)
        self.ai = AIPlayer(AI_NAME, self.rng)
        self.turn = 0

    def setup(self) -> None:
        self.io.say("\n--- Fleet Placement ---")
        self.io.say("Ships extend from the starting coordinate: "
                    "H = right, V = down, L = left, U = up.")
        self.human.place_fleet(self.rng)
        self.ai.place_fleet()
        self.io.say(f"\nThe {AI_NAME} has hidden its fleet. Time to go fishin'.")

    def play(self) -> Player:
        """Run setup and the turn loop; return the winning player."""
        self.setup()
        while True:
            self.turn += 1
            self.io.show_side_by_side(
                self.human.board, f"{PLAYER_NAME} (your waters)",
                self.ai.board, f"Tracking: {AI_NAME}",
            )
            self.io.say(f"\n=== Turn {self.turn} ===")

            if self._player_turn():
                return self.human
            if self._ai_turn():
                return self.ai

    def _player_turn(self) -> bool:
        """Handle the human's shot. Returns True if the human has won."""
        target = self.human.choose_target(self.ai.board)
        result, ship = self.ai.board.receive_shot(target)
        self.io.say(f"\nYou fire at {coord_to_str(target)}...")
        if result is not ShotResult.MISS:
            self.sound.explosion()
            self.io.say("*** KA-BOOM! ***")
        if result is ShotResult.MISS:
            self.io.say(self.rng.choice(PLAYER_MISS_LINES))
        elif result is ShotResult.HIT:
            self.io.say(self.rng.choice(PLAYER_HIT_LINES))
        else:
            self.io.say(f"Hit! You sunk my {ship.name}! (the \"{ship.nickname}\")")
            self.io.say(f"  {AI_NAME}: \"Aw, jeez. That one's goin' to the bottom.\"")
        return self.ai.has_lost()

    def _ai_turn(self) -> bool:
        """Handle the AI's shot. Returns True if the AI has won."""
        target = self.ai.choose_target(self.human.board)
        result, ship = self.human.board.receive_shot(target)
        self.ai.record_result(target, result, ship)
        self.io.say(f"\nThe {AI_NAME} fires at {coord_to_str(target)}...")
        if result is ShotResult.MISS:
            self.io.say(self.rng.choice(AI_MISS_LINES))
        elif result is ShotResult.HIT:
            self.io.say(f"{self.rng.choice(AI_HIT_LINES)} Your {ship.display_name} is damaged.")
        else:
            self.io.say(f"Hit! They sunk your {ship.display_name}! Uff da!")
        return self.human.has_lost()

    def announce_winner(self, winner: Player) -> None:
        self.io.show_side_by_side(
            self.human.board, f"{PLAYER_NAME} (your waters)",
            self.ai.board, f"Final: {AI_NAME}",
        )
        self.io.say("\n" + "=" * 62)
        if winner is self.human:
            self.io.say(f" VICTORY! All {TOTAL_SEGMENTS} enemy segments sunk in {self.turn} turns.")
            self.io.say(" The Cheesehead Navy limps home. Hotdish for everyone!")
            self.io.say(" The North Shore is safe. Skol, Captain!")
        else:
            self.io.say(f" DEFEAT. All {TOTAL_SEGMENTS} of your segments were sunk.")
            self.io.say(" The Cheesehead Navy wins this one. Oh, for cryin' out loud.")
            self.io.say(" There's always next season, don'tcha know.")
        self.io.say("=" * 62)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def ask_play_again(io: GameIO) -> bool:
    """Ask whether to play again; loop until a clear yes/no answer."""
    while True:
        answer = io.ask("\nPlay again? (Y/N): ").strip().upper()
        if answer in ("Y", "YES", "YAH", "YOU BETCHA"):
            return True
        if answer in ("N", "NO", "NOPE"):
            return False
        io.say("Ope, just a Y or an N, please.")


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Battleship: Land of 10,000 Lakes Edition")
    parser.add_argument("--no-sound", action="store_true",
                        help="don't play the explosion sound on hits")
    args = parser.parse_args(argv)

    io = GameIO()
    sound = SoundEffects(enabled=not args.no_sound)
    io.say(BANNER)
    try:
        while True:
            game = BattleshipGame(io, sound=sound)
            winner = game.play()
            game.announce_winner(winner)
            if not ask_play_again(io):
                io.say(GOODBYE)
                return 0
    except (KeyboardInterrupt, EOFError):
        io.say("\n\nLeavin' already? Okay then, bye now!")
        return 0


if __name__ == "__main__":
    sys.exit(main())
