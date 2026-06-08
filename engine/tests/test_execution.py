"""Unit tests for position-transition planning."""

import unittest

from sunday import execution as ex


class TestPlanTransition(unittest.TestCase):
    def test_hold_when_aligned(self):
        self.assertEqual(ex.plan_transition("long", "long"), ex.HOLD)
        self.assertEqual(ex.plan_transition(None, None), ex.HOLD)        # flat, want flat

    def test_open_from_flat(self):
        self.assertEqual(ex.plan_transition(None, "long"), ex.OPEN_LONG)
        self.assertEqual(ex.plan_transition(None, "short"), ex.OPEN_SHORT)

    def test_close_when_target_flat(self):
        self.assertEqual(ex.plan_transition("long", None), ex.CLOSE)
        self.assertEqual(ex.plan_transition("short", None), ex.CLOSE)

    def test_flip_to_opposite(self):
        self.assertEqual(ex.plan_transition("short", "long"), ex.FLIP_LONG)
        self.assertEqual(ex.plan_transition("long", "short"), ex.FLIP_SHORT)

    def test_is_entry_action(self):
        self.assertTrue(ex.is_entry_action(ex.OPEN_LONG))
        self.assertTrue(ex.is_entry_action(ex.FLIP_SHORT))
        self.assertFalse(ex.is_entry_action(ex.CLOSE))
        self.assertFalse(ex.is_entry_action(ex.HOLD))


if __name__ == "__main__":
    unittest.main()
