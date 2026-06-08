"""P3 cleanup: typed env helpers + effective-config logging (utils.env).

Guards the shared parsing semantics — notably env_bool accepting true/yes/on
(the old == "1" foot-gun) — and the effective_config record used by
utils.config.log_effective_config.
"""

from __future__ import annotations

import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))


class EnvBoolTests(unittest.TestCase):
    def setUp(self):
        from utils import env
        env.reset_for_testing()

    def test_truthy_spellings(self):
        from utils.env import env_bool
        for val in ("1", "true", "TRUE", "Yes", "on", " on "):
            with mock.patch.dict(os.environ, {"X": val}):
                self.assertTrue(env_bool("X"), val)

    def test_falsy_spellings(self):
        from utils.env import env_bool
        for val in ("0", "false", "no", "off", ""):
            with mock.patch.dict(os.environ, {"X": val}):
                self.assertFalse(env_bool("X", default=True) if val == "" else env_bool("X"))

    def test_unset_uses_default(self):
        from utils.env import env_bool
        os.environ.pop("MISSING", None)
        self.assertTrue(env_bool("MISSING", default=True))
        self.assertFalse(env_bool("MISSING", default=False))

    def test_unrecognised_warns_and_uses_default(self):
        from utils.env import env_bool
        with mock.patch.dict(os.environ, {"X": "maybe"}):
            self.assertTrue(env_bool("X", default=True))
            self.assertFalse(env_bool("X", default=False))


class EnvNumericTests(unittest.TestCase):
    def test_int_parses_and_defaults(self):
        from utils.env import env_int
        with mock.patch.dict(os.environ, {"N": "42"}):
            self.assertEqual(env_int("N", 7), 42)
        with mock.patch.dict(os.environ, {"N": "notint"}):
            self.assertEqual(env_int("N", 7), 7)
        os.environ.pop("N", None)
        self.assertEqual(env_int("N", 7), 7)

    def test_empty_string_is_default(self):
        from utils.env import env_float, env_int
        with mock.patch.dict(os.environ, {"N": "", "F": ""}):
            self.assertEqual(env_int("N", 5), 5)
            self.assertEqual(env_float("F", 1.5), 1.5)

    def test_float_parses(self):
        from utils.env import env_float
        with mock.patch.dict(os.environ, {"F": "2.5"}):
            self.assertEqual(env_float("F", 1.0), 2.5)


class EffectiveConfigTests(unittest.TestCase):
    def setUp(self):
        from utils import env
        env.reset_for_testing()

    def test_records_only_overrides_by_default(self):
        from utils.env import effective_config, env_bool, env_int
        os.environ.pop("UNSET_FLAG", None)
        env_bool("UNSET_FLAG", default=False)          # default, not recorded as override
        with mock.patch.dict(os.environ, {"SET_N": "9"}):
            env_int("SET_N", 1)
        cfg = effective_config()
        self.assertIn("SET_N", cfg)
        self.assertEqual(cfg["SET_N"], 9)
        self.assertNotIn("UNSET_FLAG", cfg)

    def test_log_effective_config_returns_overrides(self):
        from utils.config import log_effective_config
        from utils.env import env_bool, reset_for_testing
        reset_for_testing()
        with mock.patch.dict(os.environ, {"FEATURE_X": "true"}):
            env_bool("FEATURE_X")
            logged = log_effective_config()
        self.assertEqual(logged.get("FEATURE_X"), True)


class MigratedCallSiteTests(unittest.TestCase):
    """A couple of real call sites now honour true/yes (not just "1")."""

    def test_browser_enabled_accepts_true(self):
        from tools.browser_scraper import browser_enabled
        with mock.patch.dict(os.environ, {"BROWSER_SCRAPER_ENABLED": "true"}):
            self.assertTrue(browser_enabled())

    def test_company_fallback_disabled_by_false(self):
        import tools.company_research as cr
        with mock.patch.object(cr, "get_active_provider", return_value="openai"), \
             mock.patch.dict(os.environ, {"COMPANY_RESEARCH_FALLBACK": "false"}):
            self.assertFalse(cr.fallback_enabled())


if __name__ == "__main__":
    unittest.main()
