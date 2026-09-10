import unittest
from pathlib import Path


class DesktopLauncherModeTests(unittest.TestCase):
    def test_primary_launcher_uses_browser_app_mode(self):
        script = (Path(__file__).resolve().parents[1] / "scripts" / "start_pandafusion_desktop.sh").read_text(
            encoding="utf-8"
        )

        self.assertIn('exec "$browser" --app="$server_url"', script)
        self.assertIn("google-chrome google-chrome-stable chromium chromium-browser", script)
        self.assertIn("PANDA_FUSION_NATIVE_EXPERIMENTAL", script)
        self.assertIn("La AppImage nativa queda disponible sólo bajo opt-in experimental.", script)


if __name__ == "__main__":
    unittest.main()
