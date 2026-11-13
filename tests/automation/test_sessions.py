"""Memory-only deterministic session-binding regressions."""
from __future__ import annotations

import unittest

from lads_automation.state import (
    JournalSessionAmbiguous,
    JournalSessionCorrupt,
    MemoryJournal,
)

SESSION = "thread-synthetic"
OLD = "11111111-1111-4111-8111-111111111111"
CURRENT = "22222222-2222-4222-8222-222222222222"
OTHER = "33333333-3333-4333-8333-333333333333"


def task(task_id, status, *, ready=False, session=SESSION):
    return {
        "task_id": task_id,
        "status": status,
        "ready": ready,
        "session_id": session,
        "event_ids": [],
    }


class SessionResolutionTests(unittest.TestCase):
    def test_terminal_task_does_not_shadow_current_unfinished_task(self):
        journal = MemoryJournal()
        journal.create_task(task(OLD, "DELIVERY_READY", ready=True))
        journal.create_task(task(CURRENT, "TASK_CREATED"))
        self.assertEqual(CURRENT, journal.resolve_session(SESSION))

    def test_more_than_one_unfinished_task_is_an_explicit_ambiguity(self):
        journal = MemoryJournal()
        journal.create_task(task(CURRENT, "TASK_CREATED"))
        journal.create_task(task(OTHER, "QA_REVIEW_VERIFIED"))
        with self.assertRaises(JournalSessionAmbiguous):
            journal.resolve_session(SESSION)

    def test_terminal_tasks_are_ignored_without_insertion_order_guessing(self):
        journal = MemoryJournal()
        journal.create_task(task(OLD, "DELIVERY_READY", ready=True))
        journal.create_task(task(OTHER, "BLOCKED"))
        self.assertIsNone(journal.resolve_session(SESSION))

    def test_nonmatching_and_invalid_sessions_do_not_bind(self):
        journal = MemoryJournal()
        journal.create_task(task(CURRENT, "TASK_CREATED"))
        self.assertIsNone(journal.resolve_session("different-thread"))
        self.assertIsNone(journal.resolve_session(""))
        self.assertIsNone(journal.resolve_session(None))

    def test_memory_corruption_fails_closed_without_selecting_a_task(self):
        journal = MemoryJournal()
        journal.create_task(task(CURRENT, "TASK_CREATED"))
        journal._tasks[OTHER] = object()
        with self.assertRaises(JournalSessionCorrupt) as raised:
            journal.resolve_session(SESSION)
        warning = raised.exception.warning()
        self.assertEqual(1, warning["unreadable_task_count"])
        self.assertNotIn(CURRENT, repr(warning))
        self.assertNotIn(OTHER, repr(warning))


if __name__ == "__main__":
    unittest.main()
