# src/gui/kwin_mouse_provider.py

import json
import logging
import shutil
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)

DBUS_SERVICE_NAME = "meikipop.cursor"
DBUS_OBJECT_PATH = "/cursor"
KWIN_SCRIPT_ID = "meikipop.cursor.kwinscript"
KWIN_SCRIPT_VERSION = "0.4.1"

_KWIN_QML = f'''import QtQuick
import org.kde.kwin

Item {{
    id: root
    property int lastX: -1
    property int lastY: -1

    Connections {{
        target: Workspace
        function onWindowAdded(window) {{
            if (window.caption.indexOf("meikipop-popup") >= 0) {{
                window.skipCloseAnimation = true;
                window.opacity = 0;
            }}
        }}
    }}

    DBusCall {{
        id: cursorDbus
        service: "{DBUS_SERVICE_NAME}"
        dbusInterface: "{DBUS_SERVICE_NAME}"
        path: "{DBUS_OBJECT_PATH}"
        method: "save_position"
    }}

    Timer {{
        running: true
        repeat: true
        interval: 16
        onTriggered: {{
            var pos = Workspace.cursorPos;
            if (pos.x !== root.lastX || pos.y !== root.lastY) {{
                root.lastX = pos.x;
                root.lastY = pos.y;
                cursorDbus.arguments = [pos.x + "," + pos.y];
                cursorDbus.call();
            }}
        }}
    }}
}}
'''

_KWIN_METADATA = json.dumps({
    "KPackageStructure": "KWin/Script",
    "KPlugin": {
        "Id": KWIN_SCRIPT_ID,
        "Name": "Meikipop cursor provider",
        "Description": "Provides cursor position to meikipop",
        "EnabledByDefault": True,
        "Version": KWIN_SCRIPT_VERSION,
    },
    "X-Plasma-API": "declarativescript",
    "X-Plasma-API-Minimum-Version": "6.0",
}, indent=4)


class KWinMouseProvider():

    def __init__(self):
        self.cursor_x = 0
        self.cursor_y = 0
        self.has_cursor_data = False
        self.glib_ctx = None
        self.bus_name = None
        self.service = None
        self.kwin_bus = None  # separate D-Bus connection for KWin scripting
        self.kwin_scripting = None
        self.mover_script_path = str(Path.home() / ".local" / "share" / "kwin" / "scripts" / "meikipop_mover.js")
        self.setup_dbus_service()
        self.verify_kwin_script_installation()
        self.setup_kwin_scripting()

    def get_position(self) -> tuple[int, int]:
        return (self.cursor_x, self.cursor_y)

    def process_events(self):
        if self.glib_ctx is not None:
            while self.glib_ctx.pending():
                self.glib_ctx.iteration(False)

    def update_position(self, pos_str: str):
        try:
            parts = pos_str.strip().split(",")
            if len(parts) >= 2:
                x, y = int(parts[0]), int(parts[1])
                self.cursor_x = x
                self.cursor_y = y
                self.has_cursor_data = True
        except (ValueError, IndexError):
            pass

    def set_popup_geometry(self, x, y, w, h):
        self.run_kwin_js(
            f'var ws=workspace.stackingOrder;'
            f'for(var i=ws.length-1;i>=0;i--){{'
            f'var win=ws[i];'
            f'if(win.caption.indexOf("meikipop-popup")>=0){{'
            f'win.frameGeometry={{x:{int(x)},y:{int(y)},width:{int(w)},height:{int(h)}}};'
            f'win.skipCloseAnimation=true;win.opacity=1.0;'
            f'break;}}}}'
        )

    def setup_kwin_scripting(self):
        try:
            import dbus as _dbus
            bus = _dbus.SessionBus()
            self.kwin_bus = bus
            self.kwin_scripting = bus.get_object('org.kde.KWin', '/Scripting')
            logger.info("KWin scripting D-Bus proxy ready.")
        except Exception as e:
            logger.error(f"Failed to connect to KWin scripting D-Bus: {e}")

    def run_kwin_js(self, js_code: str):
        if self.kwin_scripting is None:
            return
        try:
            Path(self.mover_script_path).write_text(js_code)
            try:
                self.kwin_scripting.unloadScript(
                    'meikipop_mover',
                    dbus_interface='org.kde.kwin.Scripting',
                )
            except Exception:
                pass
            sid = self.kwin_scripting.loadScript(
                self.mover_script_path,
                'meikipop_mover',
                dbus_interface='org.kde.kwin.Scripting',
                signature='ss',
            )
            script_obj = self.kwin_bus.get_object(
                'org.kde.KWin', f'/Scripting/Script{sid}'
            )
            import dbus as _dbus
            _dbus.Interface(script_obj, 'org.kde.kwin.Script').run()
        except Exception as e:
            logger.debug(f"KWin JS script execution failed: {e}")

    def setup_dbus_service(self):
        try:
            import dbus
            import dbus.service
            from dbus.mainloop.glib import DBusGMainLoop
            from gi.repository import GLib

            DBusGMainLoop(set_as_default=True)
            self.glib_ctx = GLib.MainContext.default()

            provider = self

            class CursorService(dbus.service.Object):
                def __init__(self, bus_name):
                    dbus.service.Object.__init__(self, bus_name, DBUS_OBJECT_PATH)

                @dbus.service.method(DBUS_SERVICE_NAME, in_signature="s", out_signature="")
                def save_position(self, m):
                    provider.update_position(str(m))

            self.bus_name = dbus.service.BusName(
                DBUS_SERVICE_NAME, dbus.SessionBus(), do_not_queue=True
            )
            self.service = CursorService(self.bus_name)
            logger.info(f"Cursor D-Bus service registered as '{DBUS_SERVICE_NAME}'")
        except Exception as e:
            logger.error(f"Failed to register cursor D-Bus service: {e}")

    def verify_kwin_script_installation(self):
        script_dir = Path.home() / ".local" / "share" / "kwin" / "scripts" / KWIN_SCRIPT_ID
        qml_path = script_dir / "contents" / "ui" / "main.qml"
        metadata_path = script_dir / "metadata.json"

        needs_install = False
        if not qml_path.exists():
            needs_install = True
        else:
            try:
                with open(metadata_path) as f:
                    existing = json.load(f)
                existing_version = existing.get("KPlugin", {}).get("Version", "0")
                if existing_version != KWIN_SCRIPT_VERSION:
                    needs_install = True
            except Exception:
                needs_install = True

        if needs_install:
            self.install_kwin_script(script_dir)

        self.enable_kwin_script()

    def install_kwin_script(self, script_dir: Path):
        try:
            if script_dir.exists():
                shutil.rmtree(script_dir)

            ui_dir = script_dir / "contents" / "ui"
            ui_dir.mkdir(parents=True, exist_ok=True)

            (ui_dir / "main.qml").write_text(_KWIN_QML)
            (script_dir / "metadata.json").write_text(_KWIN_METADATA)

            logger.info(f"Installed KWin cursor script to {script_dir}")
        except Exception as e:
            logger.error(f"Failed to install KWin cursor script: {e}")

    def enable_kwin_script(self):
        import time
        try:
            config_key = f"{KWIN_SCRIPT_ID}Enabled"
            subprocess.run(
                ["kwriteconfig6", "--file", "kwinrc", "--group", "Plugins",
                 "--key", config_key, "false"],
                capture_output=True, timeout=5
            )
            subprocess.run(
                ["dbus-send", "--session", "--dest=org.kde.KWin",
                 "--type=method_call", "/KWin", "org.kde.KWin.reconfigure"],
                capture_output=True, timeout=5
            )
            time.sleep(0.3)
            subprocess.run(
                ["kwriteconfig6", "--file", "kwinrc", "--group", "Plugins",
                 "--key", config_key, "true"],
                check=True, capture_output=True, timeout=5
            )
            subprocess.run(
                ["dbus-send", "--session", "--dest=org.kde.KWin",
                 "--type=method_call", "/KWin", "org.kde.KWin.reconfigure"],
                check=True, capture_output=True, timeout=5
            )
            logger.info("KWin script force-(re)loaded and enabled.")
        except FileNotFoundError:
            logger.warning("kwriteconfig6 not found. KWin script may need manual activation.")
        except Exception as e:
            logger.error(f"Failed to enable KWin cursor script: {e}")

    def cleanup(self):
        """Disable the KWin scripts, but leave installed for next run."""
        if self.kwin_scripting is not None:
            try:
                self.kwin_scripting.unloadScript(
                    'meikipop_mover',
                    dbus_interface='org.kde.kwin.Scripting',
                )
            except Exception:
                pass
        try:
            config_key = f"{KWIN_SCRIPT_ID}Enabled"
            subprocess.run(
                ["kwriteconfig6", "--file", "kwinrc", "--group", "Plugins",
                 "--key", config_key, "false"],
                capture_output=True, timeout=5
            )
            subprocess.run(
                ["dbus-send", "--session", "--dest=org.kde.KWin",
                 "--type=method_call", "/KWin", "org.kde.KWin.reconfigure"],
                capture_output=True, timeout=5
            )
            logger.info("KWin cursor script disabled.")
        except Exception:
            pass


def create_mouse_provider() -> KWinMouseProvider:
    """Create mouse provider for KDE Wayland."""
    from src.config.config import IS_WAYLAND
    if IS_WAYLAND:
        logger.info("Wayland session detected. Using KWin D-Bus mouse provider.")
        return KWinMouseProvider()
