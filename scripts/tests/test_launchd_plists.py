"""Guards on the launchd plist templates.

Both jobs shell out to `claude`, which lives outside launchd's default
PATH (/usr/bin:/bin:/usr/sbin:/sbin). A template missing its PATH block
fails silently in production — the ping reports a scheduled-guess window
and the bot answers from the schedule — so assert the block is present.
"""
import os
import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

ROOT = Path(__file__).resolve().parents[2]
LAUNCHD_DIR = ROOT / "launchd"
PING_PLIST = LAUNCHD_DIR / "com.claude-session-ping.plist"
BOT_PLIST = LAUNCHD_DIR / "com.claude-session-ping.telegram-bot.plist"
INSTALL_SH = ROOT / "install.sh"


class TestPlistEnvironment(unittest.TestCase):
    def test_both_plists_put_claude_on_path(self):
        for plist in (PING_PLIST, BOT_PLIST):
            text = plist.read_text()
            with self.subTest(plist=plist.name):
                self.assertIn("EnvironmentVariables", text)
                self.assertIn("<key>PATH</key>", text)
                # claude installs to ~/.local/bin; launchd's default PATH omits it.
                self.assertIn("{{HOME_DIR}}/.local/bin", text)

    def test_both_plists_set_user_for_claude_login(self):
        # Without USER/LOGNAME `claude` reports "Not logged in" under launchd.
        for plist in (PING_PLIST, BOT_PLIST):
            text = plist.read_text()
            with self.subTest(plist=plist.name):
                self.assertIn("<key>USER</key>", text)
                self.assertIn("<key>LOGNAME</key>", text)


class TestInstallSubstitutions(unittest.TestCase):
    def test_install_substitutes_every_placeholder_used(self):
        """Every {{FOO}} in a template must be sed-substituted by install.sh.

        install.sh has a separate sed line per plist, so a template can
        outgrow its installer and ship a literal {{USER}} into launchd.
        """
        install_text = INSTALL_SH.read_text()
        for plist in (PING_PLIST, BOT_PLIST):
            placeholders = set(re.findall(r"\{\{(\w+)\}\}", plist.read_text()))
            self.assertTrue(placeholders, f"{plist.name} has no placeholders?")
            # Find the sed line that installs this plist.
            sed_lines = [
                line for line in install_text.splitlines()
                if "sed -e" in line and (
                    "BOT_PLIST_TEMPLATE" in line if plist is BOT_PLIST
                    else "$PLIST_TEMPLATE" in line
                )
            ]
            self.assertEqual(len(sed_lines), 1, f"expected one sed line for {plist.name}")
            for name in placeholders:
                with self.subTest(plist=plist.name, placeholder=name):
                    self.assertIn(f"{{{{{name}}}}}", sed_lines[0])


if __name__ == "__main__":
    unittest.main()
