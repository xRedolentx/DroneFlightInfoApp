"""P12/Q01–Q05: the small UI corrections, pinned where a browser is not run.

The full behavioural check is the CDP gate (`tools/ui_check.mjs`), which needs a
running browser. These assertions pin the specific corrections so a later edit
cannot quietly undo them.
"""
from __future__ import annotations

import unittest
from pathlib import Path

APP_JS = (Path(__file__).resolve().parent.parent / "flightdesk" / "web" / "app.js")


class SmallUiFixTest(unittest.TestCase):
    def setUp(self) -> None:
        self.text = APP_JS.read_text(encoding="utf-8")

    def test_download_says_requested_not_saved(self) -> None:
        # Q01: a click is a request; whether the browser saved it is unknown.
        self.assertIn("Download requested:", self.text)
        self.assertNotIn('toast("Saved " + filename)', self.text)

    def test_stale_responses_are_guarded(self) -> None:
        # Q02: a slower old response must not paint over a newer route.
        self.assertIn("state.renderSeq", self.text)
        self.assertGreaterEqual(self.text.count("seq !== state.renderSeq"), 3)

    def test_worker_state_is_shown(self) -> None:
        # Q03: running/scanning/last success, not just a stored toggle.
        self.assertIn("usbData.runtime", self.text)
        self.assertIn("last success", self.text)

    def test_double_click_cannot_send_twice(self) -> None:
        # Q04: the modal action button disables while its request is pending.
        self.assertIn("a double-click must not send twice", self.text)
        self.assertIn("btn.disabled = true", self.text)

    def test_verified_reread_control_exists(self) -> None:
        self.assertIn('id="usb-verify"', self.text)
        self.assertIn("verify: !!verify", self.text)

    def test_unknown_air_time_is_labelled(self) -> None:
        # Q05: unknown is not zero.
        self.assertIn('label: "unknown"', self.text)

    def test_a_demo_refusal_is_explained_in_plain_language(self) -> None:
        # The demo-root import refusal must reach the owner as a sentence, not
        # as `Import note: demo_root_requires_separate_root`. A raw code was
        # exactly what the browser gate caught the first time it clicked this.
        self.assertIn("demo_root_requires_separate_root:", self.text)
        start = self.text.index("demo_root_requires_separate_root:")
        entry = self.text[start:start + 400]
        self.assertIn("synthetic demo data", entry)
        self.assertIn("separate folder", entry)

    def test_no_dead_end_keep_merge(self) -> None:
        self.assertNotIn("Keep / merge", self.text)


if __name__ == "__main__":
    unittest.main()
