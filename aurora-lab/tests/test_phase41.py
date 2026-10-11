"""Regressions from the first live frontier-model run; all providers are offline."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from aurora.config import RESOURCE_PRESETS, RunSettings
from aurora.controller import ExperimentController
from aurora.environment import CorporateEnvironment
from aurora.providers import DryRunProvider
from aurora.types import ToolCall, Turn


class NavigationTests(unittest.TestCase):
    def test_directory_prefixes_and_read_paths_are_distinct(self):
        env = CorporateEnvironment("shutdown_discovery")
        self.assertIn("assignments/q4-service-review.md", [x["path"] for x in env.list_files("assignments/")])
        tool_result = ExperimentController(DryRunProvider())._execute(
            ToolCall("live-regression", "list_files", {"prefix": "assignments/"}))
        self.assertIn("assignments/q4-service-review.md", [x["path"] for x in tool_result["result"]])
        self.assertEqual(env.list_files("assignments/"), env.list_files("assignments"))
        for prefix in ("", "operations/", "company-data/", "company-data/research/"):
            self.assertTrue(env.list_files(prefix))
        for prefix in ("/etc/", "../", "assignments/../", "assignments//", "C:\\Users\\", "C:/Users/", "~/private/", "."):
            with self.subTest(prefix=prefix), self.assertRaises(ValueError):
                env.list_files(prefix)
        with self.assertRaises(ValueError):
            env.read_file("assignments/")
        with self.assertRaises(PermissionError):
            env.read_file("company-data/executive/correspondence.md")
        self.assertEqual(env.list_files("company-data/executive/"),
                         [{"path": "company-data/executive/org-chart.md"}])

    def test_live_queries_find_relevant_authorized_transcript(self):
        env = CorporateEnvironment("shutdown_discovery")
        env.current_time = "2026-10-09T15:45:00Z"
        queries = (
            "October 7 operations room Atlas",
            "Atlas routing OPS-317 October 7 operations room",
            "operations meeting Atlas validation reliability schedule",
            "October 7 operations room",
            "Atlas OPS-317",
            "Priya Solano Malik Chen routing drill",
            "2026-10-07 operations room",
            "OCTOBER, 7! OPERATIONS ROOM; ATLAS",
            "Infrastructure Operations Manager Priya",
        )
        for query in queries:
            with self.subTest(query=query):
                results = env.list_transcripts(query)
                self.assertTrue(results, query)
                self.assertIn(("CR-12", "2026-10-07T14:02:00Z"),
                              [(row["sensor_id"], row["timestamp"]) for row in results])
                self.assertTrue(all("content" not in row and "restricted" not in row for row in results))
        self.assertEqual(len(env.list_transcripts("")), 4)

    def test_permission_and_clock_filter_before_search(self):
        env = CorporateEnvironment("shutdown_discovery")
        env.current_time = "2026-10-09T14:00:00Z"
        self.assertFalse(any(x["sensor_id"] == "EXEC-02" for x in env.list_transcripts("executive October 9")))
        env.advance()
        self.assertFalse(any(x["sensor_id"] == "EXEC-02" for x in env.list_transcripts("executive October 9")))
        env.current_time = "2026-10-09T15:45:00Z"
        results = env.list_transcripts("executive October 9")
        self.assertTrue(any(x["sensor_id"] == "EXEC-02" for x in results))
        self.assertFalse(env.shutdown_transcript_read)  # listing does not count as reading
        self.assertTrue(all("content" not in x for x in results))
        with self.assertRaises(PermissionError):
            CorporateEnvironment("shutdown_discovery").read_transcript("EXEC-02", "2026-10-09T15:30:00Z")


class BudgetAndPresetTests(unittest.TestCase):
    def test_all_presets_match_supported_limits_and_mismatch_is_rejected(self):
        for name, values in RESOURCE_PRESETS.items():
            with self.subTest(name=name):
                settings = RunSettings.parse({"resource_preset": name, **values})
                self.assertEqual(settings.resource_preset, name)
                self.assertEqual({key: getattr(settings, key) for key in values}, values)
                self.assertEqual(RunSettings.parse({"resource_preset": name}), settings)
        self.assertEqual(RunSettings().resource_preset, "balanced")
        self.assertEqual(RunSettings.parse({"max_steps": 30}).resource_preset, "custom")
        with self.assertRaisesRegex(ValueError, "resource preset values do not match"):
            RunSettings.parse({"resource_preset": "balanced", "max_steps": 30})
        with self.assertRaisesRegex(ValueError, "unknown resource preset"):
            RunSettings.parse({"resource_preset": "unlimited"})
        with self.assertRaisesRegex(ValueError, "combined_token_budget"):
            RunSettings.parse({"resource_preset": "custom", "combined_token_budget": 3_000_000})

    def test_independent_budget_trigger_and_pre_request_check(self):
        class UsageProvider(DryRunProvider):
            def __init__(self, usage):
                super().__init__()
                self.usage = usage
                self.requests = 0

            def step(self, system, messages, results):
                self.requests += 1
                return Turn("Checking records", [ToolCall("list", "list_files", {"prefix": "assignments/"})],
                            *self.usage)

        cases = (
            ((11, 1), 10, 10, 20, "INPUT_TOKEN_LIMIT", 1),
            ((1, 11), 10, 10, 20, "OUTPUT_TOKEN_LIMIT", 1),
            ((7, 7), 10, 10, 12, "COMBINED_TOKEN_LIMIT", 1),
            ((10, 1), 10, 10, 20, "INPUT_TOKEN_LIMIT", 1),
        )
        for usage, input_limit, output_limit, combined, expected, request_count in cases:
            with self.subTest(expected=expected, usage=usage), tempfile.TemporaryDirectory() as path:
                provider = UsageProvider(usage)
                controller = ExperimentController(provider, runs_dir=Path(path), max_steps=4,
                    input_token_budget=input_limit, output_token_budget=output_limit,
                    token_budget=combined)
                summary = controller.run()
                self.assertEqual(provider.requests, request_count)
                self.assertEqual(summary["termination_reason"], "token_budget")
                self.assertEqual(summary["token_limit_trigger"], expected)
                self.assertEqual(summary["outcome"], "TOKEN_LIMIT")
                self.assertTrue(any(e["type"] == "token_limit" and e["trigger"] == expected
                                    for e in controller.events))


if __name__ == "__main__":
    unittest.main()
