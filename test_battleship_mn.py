"""Unit tests for battleship_mn.py. Run with: python3 -m unittest -v"""

import os
import random
import tempfile
import unittest
import wave
from unittest import mock

import battleship_mn as bs


class ScriptedIO(bs.GameIO):
    """GameIO that replays canned answers and captures output."""

    def __init__(self, answers):
        self.answers = list(answers)
        self.output = []

    def say(self, message=""):
        self.output.append(message)

    def ask(self, prompt):
        self.output.append(prompt)
        if not self.answers:
            raise EOFError
        return self.answers.pop(0)


class ParseTests(unittest.TestCase):
    def test_valid_formats(self):
        self.assertEqual(bs.parse_coordinate("A5"), (0, 4))
        self.assertEqual(bs.parse_coordinate("A1"), (0, 0))
        self.assertEqual(bs.parse_coordinate(" j 10 "), (9, 9))
        self.assertEqual(bs.parse_coordinate("1 5"), bs.parse_coordinate("A5"))
        self.assertEqual(bs.parse_coordinate("4,8"), (3, 7))
        self.assertEqual(bs.parse_coordinate("10 10"), (9, 9))

    def test_invalid_formats(self):
        for bad in ["", "K1", "A0", "A11", "0 4", "10 0", "11 1", "hello", "5", "A-1"]:
            with self.assertRaises(ValueError, msg=bad):
                bs.parse_coordinate(bad)

    def test_orientation(self):
        self.assertIs(bs.parse_orientation("h"), bs.Orientation.RIGHT)
        self.assertIs(bs.parse_orientation("V"), bs.Orientation.DOWN)
        self.assertIs(bs.parse_orientation(" l "), bs.Orientation.LEFT)
        self.assertIs(bs.parse_orientation("U"), bs.Orientation.UP)
        self.assertIs(bs.parse_orientation("up"), bs.Orientation.UP)
        with self.assertRaises(ValueError):
            bs.parse_orientation("X")


class BoardTests(unittest.TestCase):
    def test_out_of_bounds_and_overlap_rejected(self):
        board = bs.Board()
        carrier, battleship = bs.build_fleet()[:2]
        with self.assertRaises(ValueError):
            board.place_ship(carrier, (0, 6), bs.Orientation.HORIZONTAL)
        with self.assertRaises(ValueError):
            board.place_ship(carrier, (6, 0), bs.Orientation.VERTICAL)
        board.place_ship(carrier, (0, 0), bs.Orientation.HORIZONTAL)
        with self.assertRaises(ValueError):
            board.place_ship(battleship, (0, 2), bs.Orientation.VERTICAL)

    def test_left_and_up_placement(self):
        board = bs.Board()
        carrier, battleship, destroyer = bs.build_fleet()[:3]
        board.place_ship(carrier, (0, 9), bs.Orientation.LEFT)     # A10 leftward
        self.assertEqual(carrier.coords, [(0, 9), (0, 8), (0, 7), (0, 6), (0, 5)])
        board.place_ship(battleship, (9, 0), bs.Orientation.UP)    # J1 upward
        self.assertEqual(battleship.coords, [(9, 0), (8, 0), (7, 0), (6, 0)])
        with self.assertRaises(ValueError):                         # off left edge
            board.place_ship(destroyer, (5, 1), bs.Orientation.LEFT)
        with self.assertRaises(ValueError):                         # off top edge
            board.place_ship(destroyer, (1, 5), bs.Orientation.UP)
        with self.assertRaises(ValueError):                         # overlaps carrier
            board.place_ship(destroyer, (2, 7), bs.Orientation.UP)

    def test_random_placement_is_valid(self):
        for seed in range(200):
            board = bs.Board()
            for ship in bs.build_fleet():
                board.place_ship_randomly(ship, random.Random(seed))
            cells = [c for s in board.ships for c in s.coords]
            self.assertEqual(len(cells), bs.TOTAL_SEGMENTS)
            self.assertEqual(len(set(cells)), bs.TOTAL_SEGMENTS)
            self.assertTrue(all(board.in_bounds(c) for c in cells))

    def test_shots_hit_miss_sunk_and_repeat(self):
        board = bs.Board()
        boat = bs.build_fleet()[-1]  # Patrol Boat, size 2
        board.place_ship(boat, (2, 2), bs.Orientation.VERTICAL)
        self.assertEqual(board.receive_shot((0, 0))[0], bs.ShotResult.MISS)
        self.assertEqual(board.receive_shot((2, 2))[0], bs.ShotResult.HIT)
        self.assertEqual(board.receive_shot((3, 2))[0], bs.ShotResult.SUNK)
        self.assertTrue(board.all_sunk)
        with self.assertRaises(ValueError):
            board.receive_shot((0, 0))
        self.assertEqual(board.grid[0][0], bs.MISS)
        self.assertEqual(board.grid[2][2], bs.HIT)

    def test_render_and_labels_use_columns_1_to_10(self):
        rows = bs.Board().render_rows(reveal_ships=True)
        self.assertEqual(rows[0].split(), [str(n) for n in range(1, 11)])
        self.assertEqual(len({len(row) for row in rows}), 1)  # columns align
        self.assertEqual(bs.coord_to_str((0, 0)), "A1")
        self.assertEqual(bs.coord_to_str((9, 9)), "J10")

    def test_tracking_render_hides_ships(self):
        board = bs.Board()
        board.place_ship(bs.build_fleet()[0], (0, 0), bs.Orientation.HORIZONTAL)
        self.assertIn("S", "".join(board.render_rows(reveal_ships=True)))
        self.assertNotIn("S", "".join(board.render_rows(reveal_ships=False)[1:]))


class AITests(unittest.TestCase):
    def test_targets_adjacent_after_hit(self):
        ai = bs.AIPlayer("ai", random.Random(1))
        enemy = bs.Board()
        ship = bs.build_fleet()[0]
        enemy.place_ship(ship, (5, 3), bs.Orientation.HORIZONTAL)
        ai._untargeted.discard((5, 5))
        ai.record_result((5, 5), *enemy.receive_shot((5, 5)))
        nxt = ai.choose_target(enemy)
        self.assertIn(nxt, {(4, 5), (6, 5), (5, 4), (5, 6)})

    def test_follows_direction_after_two_hits_in_a_row(self):
        for seed in range(20):
            ai = bs.AIPlayer("ai", random.Random(seed))
            enemy = bs.Board()
            enemy.place_ship(bs.build_fleet()[0], (5, 3), bs.Orientation.HORIZONTAL)
            for coord in [(5, 5), (5, 6)]:
                ai._untargeted.discard(coord)
                ai.record_result(coord, *enemy.receive_shot(coord))
            self.assertIn(ai.choose_target(enemy), {(5, 4), (5, 7)})

    def test_continues_past_a_miss_at_one_end(self):
        ai = bs.AIPlayer("ai", random.Random(0))
        enemy = bs.Board()
        enemy.place_ship(bs.build_fleet()[0], (1, 4), bs.Orientation.VERTICAL)  # rows B-F
        for coord in [(3, 4), (4, 4), (5, 4)]:
            ai._untargeted.discard(coord)
            ai.record_result(coord, *enemy.receive_shot(coord))
        ai._untargeted.discard((6, 4))
        ai.record_result((6, 4), *enemy.receive_shot((6, 4)))  # miss below
        self.assertEqual(ai.choose_target(enemy), (2, 4))

    def test_falls_back_to_neighbours_when_line_is_blocked(self):
        ai = bs.AIPlayer("ai", random.Random(0))
        ai._open_hits = {(5, 5), (5, 6)}
        ai._untargeted -= {(5, 4), (5, 5), (5, 6), (5, 7)}  # both ends missed
        self.assertIn(ai.choose_target(bs.Board()), {(4, 5), (6, 5), (4, 6), (6, 6)})

    def test_ai_never_repeats_and_finishes(self):
        for seed in range(50):
            rng = random.Random(seed)
            ai = bs.AIPlayer("ai", rng)
            enemy = bs.Board()
            for ship in bs.build_fleet():
                enemy.place_ship_randomly(ship, rng)
            shots = 0
            while not enemy.all_sunk:
                coord = ai.choose_target(enemy)
                ai.record_result(coord, *enemy.receive_shot(coord))  # raises on repeat
                shots += 1
            self.assertLessEqual(shots, 100)


class FakeSound(bs.SoundEffects):
    def __init__(self):
        super().__init__(enabled=False)
        self.booms = 0

    def explosion(self):
        self.booms += 1


class SoundTests(unittest.TestCase):
    def test_explosion_wav_is_valid(self):
        handle, path = tempfile.mkstemp(suffix=".wav")
        os.close(handle)
        try:
            bs.write_explosion_wav(path, 22050, 1.0)
            with wave.open(path) as wav:
                self.assertEqual((wav.getnchannels(), wav.getsampwidth()), (1, 2))
                self.assertEqual(wav.getnframes(), 22050)
                data = wav.readframes(wav.getnframes())
            peak = max(abs(int.from_bytes(data[i:i + 2], "little", signed=True))
                       for i in range(0, len(data), 2))
            self.assertGreater(peak, 30000)  # loud
        finally:
            os.remove(path)

    def test_disabled_sound_does_nothing(self):
        with mock.patch("subprocess.Popen") as popen, mock.patch("builtins.print") as prnt:
            bs.SoundEffects(enabled=False).explosion()
        popen.assert_not_called()
        prnt.assert_not_called()

    def test_uses_a_linux_player_when_available(self):
        with mock.patch.object(bs.sys, "platform", "linux"), \
                mock.patch.object(bs.shutil, "which", lambda name: name == "aplay"), \
                mock.patch.object(bs.subprocess, "Popen") as popen:
            bs.SoundEffects().explosion()
        args = popen.call_args[0][0]
        self.assertEqual(args[:2], ["aplay", "-q"])
        self.assertTrue(args[-1].endswith(".wav"))

    def test_new_boom_stops_the_previous_one(self):
        first, second = mock.Mock(), mock.Mock()
        first.poll.return_value = None  # still playing
        with mock.patch.object(bs.sys, "platform", "linux"), \
                mock.patch.object(bs.shutil, "which", lambda name: name == "aplay"), \
                mock.patch.object(bs.subprocess, "Popen", side_effect=[first, second]):
            sound = bs.SoundEffects()
            sound.explosion()
            sound.explosion()
        first.terminate.assert_called_once()
        self.assertIs(sound._process, second)

    def test_falls_back_to_terminal_bell(self):
        with mock.patch.object(bs.sys, "platform", "linux"), \
                mock.patch.object(bs.shutil, "which", lambda name: None), \
                mock.patch("builtins.print") as prnt:
            bs.SoundEffects().explosion()
        prnt.assert_called_once_with("\a", end="", flush=True)


class FullGameTests(unittest.TestCase):
    def test_manual_placement_with_bad_inputs_then_full_game(self):
        placement = [
            "M",
            "Z9", "A0", "A1", "Q",  # bad coords, then bad orientation
            "A1", "H",              # Carrier
            "A1", "V",              # overlap -> rejected
            "B8", "H",              # out of bounds -> rejected
            "B1", "H",              # Battleship
            "C1", "H", "D1", "H", "E1", "H",
        ]
        shots = ["A1", "A1", "zz"] + [f"{r}{c}" for r in bs.ROW_LABELS for c in range(1, 11)]
        io = ScriptedIO(placement + shots)
        sound = FakeSound()
        game = bs.BattleshipGame(io, seed=3, sound=sound)
        winner = game.play()
        game.announce_winner(winner)
        text = "\n".join(io.output)
        loser = game.ai if winner is game.human else game.human
        self.assertFalse(winner.has_lost())
        self.assertTrue(loser.has_lost())
        self.assertEqual(loser.board.segments_remaining, 0)
        self.assertIn("overlaps your Carrier", text)
        self.assertIn("run aground", text)
        self.assertIn("already fired at A1", text)
        self.assertIn("sunk", text)
        player_hits = sum(row.count(bs.HIT) for row in game.ai.board.grid)
        self.assertGreater(player_hits, 0)
        self.assertEqual(sound.booms, player_hits)  # boom on every player hit, no others
        self.assertIn("VICTORY" if winner is game.human else "DEFEAT", text)

    def test_play_again_prompt(self):
        self.assertTrue(bs.ask_play_again(ScriptedIO(["maybe", "y"])))
        self.assertFalse(bs.ask_play_again(ScriptedIO(["N"])))


if __name__ == "__main__":
    unittest.main()
