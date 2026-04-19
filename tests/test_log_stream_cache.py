import unittest

from utils.log_stream_cache import RecentParsedLogCache


class RecentParsedLogCacheTests(unittest.TestCase):
    def test_reuses_parsed_logs_when_recent_window_is_unchanged(self):
        cache = RecentParsedLogCache(limit=3)
        logs = [
            "[10:00:00] [info] first",
            "[10:00:01] [warning] second",
        ]

        first_recent, first_parsed, changed = cache.refresh(logs)
        self.assertTrue(changed)
        self.assertEqual(first_recent, logs)
        self.assertEqual(len(first_parsed), 2)

        second_recent, second_parsed, changed = cache.refresh(list(logs))
        self.assertFalse(changed)
        self.assertEqual(second_recent, logs)
        self.assertIs(second_parsed, first_parsed)

    def test_only_parses_new_entries_when_window_grows(self):
        cache = RecentParsedLogCache(limit=3)
        initial_logs = [
            "[10:00:00] [info] first",
            "[10:00:01] [warning] second",
        ]

        _, first_parsed, _ = cache.refresh(initial_logs)
        updated_logs = initial_logs + ["[10:00:02] [error] third"]

        recent, second_parsed, changed = cache.refresh(updated_logs)

        self.assertTrue(changed)
        self.assertEqual(recent, updated_logs[-3:])
        self.assertEqual(len(second_parsed), 3)
        self.assertIs(second_parsed[0], first_parsed[0])
        self.assertIs(second_parsed[1], first_parsed[1])
        self.assertEqual(second_parsed[2]["level"], "ERROR")
        self.assertEqual(second_parsed[2]["text"], "third")

    def test_reuses_overlap_when_recent_window_slides(self):
        cache = RecentParsedLogCache(limit=3)
        initial_logs = [
            "[10:00:00] [info] first",
            "[10:00:01] [warning] second",
            "[10:00:02] [error] third",
        ]

        _, first_parsed, _ = cache.refresh(initial_logs)
        updated_logs = initial_logs + ["[10:00:03] [info] fourth"]

        recent, second_parsed, changed = cache.refresh(updated_logs)

        self.assertTrue(changed)
        self.assertEqual(recent, updated_logs[-3:])
        self.assertEqual(len(second_parsed), 3)
        self.assertIs(second_parsed[0], first_parsed[1])
        self.assertIs(second_parsed[1], first_parsed[2])
        self.assertEqual(second_parsed[2]["text"], "fourth")


if __name__ == "__main__":
    unittest.main()
