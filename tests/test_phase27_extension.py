"""Phase 27: Chrome extension — manifest validity, wiring, and JS helper tests.

We can't run a headless Chrome here, but we CAN: validate the MV3 manifest is
well-formed and declares the right permissions, assert the popup wires to the
REST API correctly, and (when Node is available) run the pure-helper JS tests.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import unittest
from pathlib import Path

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

_EXT = Path(__file__).resolve().parents[1] / "chrome-extension"


class ManifestTests(unittest.TestCase):
    def test_manifest_is_valid_mv3(self):
        manifest = json.loads((_EXT / "manifest.json").read_text())
        self.assertEqual(manifest["manifest_version"], 3)
        self.assertEqual(manifest["name"], "JobOffer Scanner")
        self.assertIn("version", manifest)

    def test_manifest_declares_required_permissions(self):
        manifest = json.loads((_EXT / "manifest.json").read_text())
        for perm in ("activeTab", "scripting", "storage"):
            self.assertIn(perm, manifest["permissions"])

    def test_manifest_points_at_existing_popup(self):
        manifest = json.loads((_EXT / "manifest.json").read_text())
        popup = manifest["action"]["default_popup"]
        self.assertTrue((_EXT / popup).exists())


class WiringTests(unittest.TestCase):
    def test_popup_loads_extract_then_popup_scripts(self):
        html = (_EXT / "popup.html").read_text()
        # extract.js must load before popup.js (popup uses its globals).
        self.assertLess(html.index("extract.js"), html.index("popup.js"))

    def test_popup_calls_v1_analyze_via_helper(self):
        extract = (_EXT / "extract.js").read_text()
        self.assertIn("/v1/analyze", extract)
        self.assertIn("Bearer", extract)

    def test_popup_uses_chrome_storage_and_scripting(self):
        popup = (_EXT / "popup.js").read_text()
        self.assertIn("chrome.storage.local", popup)
        self.assertIn("chrome.scripting.executeScript", popup)

    def test_required_files_present(self):
        for f in ("manifest.json", "popup.html", "popup.js", "extract.js", "README.md"):
            self.assertTrue((_EXT / f).exists(), f"missing {f}")


class JsHelperTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which("node"), "node not available")
    def test_node_helper_suite_passes(self):
        result = subprocess.run(
            ["node", "extract.test.mjs"],
            cwd=_EXT, capture_output=True, text=True,
        )
        self.assertEqual(
            result.returncode, 0,
            msg=f"node tests failed:\nSTDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}",
        )
        self.assertIn("tests passed", result.stdout)


if __name__ == "__main__":
    unittest.main()
