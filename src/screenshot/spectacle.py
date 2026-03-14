# src/screenshot/spectacle.py
import logging
import os
import subprocess
from src.config.config import IS_WAYLAND

from PIL import Image

logger = logging.getLogger(__name__)

class SpectacleBackend():

    @staticmethod
    def capture(monitor: dict) -> Image.Image:
        import tempfile
        with tempfile.NamedTemporaryFile(suffix='.png', delete=False) as f:
            tmp_path = f.name

        auto_save_dir = SpectacleBackend.get_spectacle_auto_save_dir()
        pre_existing = set()
        if auto_save_dir and auto_save_dir.is_dir():
            pre_existing = set(auto_save_dir.iterdir())

        try:
            result = subprocess.run(
                ['spectacle', '--background', '--nonotify', '--fullscreen',
                 '--output', tmp_path],
                capture_output=True, text=True, timeout=10
            )
            if result.returncode != 0:
                raise RuntimeError(f"spectacle failed (exit {result.returncode}): {result.stderr.strip()}")

            img = SpectacleBackend.load_and_cleanup(tmp_path)

            SpectacleBackend.cleanup_spectacle_auto_save(auto_save_dir, pre_existing)

            x, y = monitor['left'], monitor['top']
            w, h = monitor['width'], monitor['height']

            img_w, img_h = img.size
            if x == 0 and y == 0 and w == img_w and h == img_h:
                return img

            return img.crop((x, y, x + w, y + h))
        except FileNotFoundError:
            raise RuntimeError("Spectacle CLI is not available.")

    @staticmethod
    def get_spectacle_auto_save_dir():
        from pathlib import Path
        import configparser
        rc_path = Path.home() / ".config" / "spectaclerc"
        if not rc_path.exists():
            return None
        try:
            cp = configparser.ConfigParser()
            cp.read(str(rc_path), encoding='utf-8')
            raw = cp.get('ImageSave', 'imageSaveLocation', fallback='')
            if raw:
                # Value is a file:// URI — strip the scheme
                loc = raw.replace('file://', '')
                return Path(loc) if loc else None
        except Exception as e:
            logger.debug(f"Could not read spectaclerc: {e}")
        return None

    @staticmethod
    def cleanup_spectacle_auto_save(auto_save_dir, pre_existing: set):
        """Delete any new file(s) Spectacle created in its auto-save directory."""
        if not auto_save_dir or not auto_save_dir.is_dir():
            return
        try:
            for f in auto_save_dir.iterdir():
                if f not in pre_existing and f.is_file():
                    logger.debug(f"Removing Spectacle auto-save: {f}")
                    f.unlink()
        except Exception as e:
            logger.debug(f"Could not clean up Spectacle auto-save dir: {e}")

    @staticmethod
    def load_and_cleanup(path: str) -> Image.Image:
        """Load an image from a temp file and delete it."""
        img = Image.open(path).convert("RGB")
        img.load()
        try:
            os.unlink(path)
        except OSError as e:
            logger.debug(f"Could not delete temp screenshot file {path}: {e}")
        return img

    @staticmethod
    def get_screens() -> list[dict]:
        from PyQt6.QtWidgets import QApplication
        screens = QApplication.screens()
        if not screens:
            logger.warning("No screens detected via Qt. Returning a default 1920x1080 entry.")
            return [{"left": 0, "top": 0, "width": 1920, "height": 1080}]

        monitors = []

        all_left = min(s.geometry().x() for s in screens)
        all_top = min(s.geometry().y() for s in screens)
        all_right = max(s.geometry().x() + s.geometry().width() for s in screens)
        all_bottom = max(s.geometry().y() + s.geometry().height() for s in screens)
        monitors.append({
            "left": all_left, "top": all_top,
            "width": all_right - all_left, "height": all_bottom - all_top
        })

        for screen in screens:
            geo = screen.geometry()
            monitors.append({
                "left": geo.x(), "top": geo.y(),
                "width": geo.width(), "height": geo.height()
            })

        return monitors
