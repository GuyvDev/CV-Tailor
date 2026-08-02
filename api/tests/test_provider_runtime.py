from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from app.services.provider_runtime import CodexRunner, ProviderUsageStore, is_quota_error


class ProviderRuntimeTests(unittest.TestCase):
    def test_quota_error_classification(self) -> None:
        self.assertTrue(is_quota_error(402, "payment required"))
        self.assertTrue(is_quota_error(429, "too many requests"))
        self.assertTrue(is_quota_error(None, "usage limit reached"))
        self.assertFalse(is_quota_error(500, "temporary server error"))

    def test_rate_limit_windows(self) -> None:
        exhausted = {
            "rateLimitsByLimitId": {
                "codex": {"primary": {"usedPercent": 100}}
            }
        }
        available = {
            "rateLimitsByLimitId": {
                "codex": {"primary": {"usedPercent": 99}}
            }
        }
        self.assertTrue(CodexRunner.rate_limits_exhausted(exhausted))
        self.assertFalse(CodexRunner.rate_limits_exhausted(available))

    def test_abacus_usage_ledger_and_cooldown(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = ProviderUsageStore(Path(temp_dir) / "usage.json")
            store.record_abacus_success(
                {
                    "usage": {
                        "prompt_tokens": 10,
                        "completion_tokens": 5,
                        "total_tokens": 15,
                    }
                }
            )
            usage = store.read()["abacus"]
            self.assertEqual(usage["requests"], 1)
            self.assertEqual(usage["total_tokens"], 15)
            store.record_abacus_quota_error("credits exhausted", status_code=402)
            self.assertTrue(store.abacus_quota_blocked())


if __name__ == "__main__":
    unittest.main()
