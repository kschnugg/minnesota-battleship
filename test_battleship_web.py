"""Tests for the browser replay wrapper (python3 -m unittest -v)."""

import unittest

import battleship_mn as mn
from battleship_web import WebSession, explosion_wav_bytes

ALL_CELLS = [f"{row}{col}" for row in mn.ROW_LABELS for col in range(1, 11)]


def play_until_play_again(session: WebSession) -> None:
    """Random fleet, then fire at every cell in order until the game ends."""
    session.answer("R")
    for cell in ALL_CELLS:
        if "Play again?" in session.prompt:
            return
        session.answer(cell)
    raise AssertionError("game did not end")


class WebSessionTests(unittest.TestCase):
    def test_starts_with_banner_and_placement_prompt(self):
        session = WebSession(seed=1)
        self.assertIn("Land of 10,000 Lakes Edition", session.text)
        self.assertIn("[M]anual / [R]andom", session.prompt)
        self.assertEqual(session.booms, 0)
        self.assertFalse(session.finished)

    def test_same_seed_and_answers_give_same_screen(self):
        first, second = WebSession(seed=5), WebSession(seed=5)
        for reply in ("R", "A1", "E5", "J10"):
            first.answer(reply)
            second.answer(reply)
        self.assertEqual(first.text, second.text)
        self.assertEqual(first.prompt, second.prompt)

    def test_answers_are_echoed_and_screen_only_grows(self):
        session = WebSession(seed=2)
        session.answer("R")
        before = session.text
        session.answer("C7")
        self.assertTrue(session.text.startswith(before))
        self.assertIn("Where do you want to fire, Captain? C7", session.text)
        self.assertIn("You fire at C7...", session.text)

    def test_bad_input_reprompts(self):
        session = WebSession(seed=3)
        session.answer("R")
        session.answer("Z99")
        self.assertIn("Where do you want to fire", session.prompt)
        session.answer("A1")
        session.answer("A1")
        self.assertIn("You already fired at A1", session.text)

    def test_manual_placement_in_all_directions(self):
        session = WebSession(seed=4)
        for reply in ("M", "A10", "L", "J1", "U", "C3", "V", "E5", "H", "J10", "U"):
            session.answer(reply)
        self.assertIn("Fleet's in the water", session.text)
        self.assertIn("Where do you want to fire", session.prompt)

    def test_booms_count_player_hits(self):
        session = WebSession(seed=6)
        play_until_play_again(session)
        self.assertGreater(session.booms, 0)
        self.assertEqual(session.booms, session.text.count("*** KA-BOOM! ***"))

    def test_play_again_keeps_history_then_quit(self):
        session = WebSession(seed=7)
        play_until_play_again(session)
        first_game, first_booms = session.text, session.booms
        session.answer("Y")
        self.assertTrue(session.text.startswith(first_game))
        self.assertEqual(session.text.count("--- Fleet Placement ---"), 2)
        self.assertEqual(session.booms, first_booms)
        play_until_play_again(session)
        session.answer("N")
        self.assertTrue(session.finished)
        self.assertEqual(session.prompt, "")
        self.assertTrue(session.text.rstrip().endswith("Bye now!"))
        session.answer("A1")  # ignored once finished
        self.assertTrue(session.finished)

    def test_explosion_wav_bytes(self):
        data = explosion_wav_bytes()
        self.assertEqual(data[:4], b"RIFF")
        self.assertEqual(data[8:12], b"WAVE")


if __name__ == "__main__":
    unittest.main()
