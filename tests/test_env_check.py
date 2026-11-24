"""P13: the environment check is bounded, honest and changes nothing."""
from __future__ import annotations

import contextlib
import io
import json
import unittest
from pathlib import Path
from unittest.mock import patch

import support  # noqa: F401 - puts the repository root on sys.path
from flightdesk import paths
from tools import check_env


class EnvCheckTest(unittest.TestCase):
    def test_reports_each_mandatory_component(self) -> None:
        result = check_env.check()
        names = {c["name"] for c in result["checks"]}
        self.assertTrue({"python", "sqlite", "parser", "powershell"} <= names)
        self.assertIn("ok", result)

    def test_a_missing_parser_fails_the_check(self) -> None:
        with patch.object(paths, "parser_exe",
                          return_value=Path("C:/definitely/not/here/dji-log.exe")):
            result = check_env.check()
        parser = next(c for c in result["checks"] if c["name"] == "parser")
        self.assertFalse(parser["ok"])
        self.assertFalse(result["ok"])

    def test_json_mode_is_machine_readable(self) -> None:
        buf = io.StringIO()
        with patch.object(paths, "parser_exe",
                          return_value=Path("C:/definitely/not/here/dji-log.exe")), \
                contextlib.redirect_stdout(buf):
            code = check_env.main(["--json"])
        self.assertEqual(code, 2)
        payload = json.loads(buf.getvalue())
        self.assertFalse(payload["ok"])
        self.assertTrue(payload["checks"])

    def test_it_never_installs_anything(self) -> None:
        # The module has no download/install calls; it is a check only.
        source = (Path(__file__).resolve().parent.parent / "tools" /
                  "check_env.py").read_text(encoding="utf-8")
        self.assertNotIn("urlretrieve", source)
        self.assertNotIn("pip install", source)


if __name__ == "__main__":
    unittest.main()
