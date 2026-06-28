"""Frontend integration checks for the new 835 Restore + Helpdesk pages and nav."""
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
PUBLIC = REPO_ROOT / "frontend_go" / "public"


class WorkstreamPageTests(unittest.TestCase):
    def test_new_pages_exist(self):
        self.assertTrue((PUBLIC / "era.html").exists())
        self.assertTrue((PUBLIC / "helpdesk.html").exists())
        self.assertTrue((PUBLIC / "static" / "js" / "era.js").exists())
        self.assertTrue((PUBLIC / "static" / "js" / "helpdesk.js").exists())

    def test_era_page_wires_restore_actions(self):
        html = (PUBLIC / "era.html").read_text(encoding="utf-8")
        for token in ("eraRedeliver", "eraReconstruct", "eraReverse", "/static/js/era.js"):
            self.assertIn(token, html)

    def test_helpdesk_page_wires_case_actions(self):
        html = (PUBLIC / "helpdesk.html").read_text(encoding="utf-8")
        for token in ("hdNotify", "hdResolve", "/static/js/helpdesk.js"):
            self.assertIn(token, html)

    def test_command_center_and_portal_link_new_pages(self):
        for name in ("index.html", "claimtrace.html", "portal.html", "processed.html"):
            html = (PUBLIC / name).read_text(encoding="utf-8")
            self.assertIn("/helpdesk.html", html, name)
            self.assertIn("/era.html", html, name)


if __name__ == "__main__":
    unittest.main()
