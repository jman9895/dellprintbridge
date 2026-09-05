import threading
import time
import urllib.request
import webbrowser

import pystray
from PIL import Image, ImageDraw

WEB_URL = "http://localhost:8631/"
CHECK_INTERVAL_SECONDS = 5

_state_lock = threading.Lock()
_bridge_running = False


def bridge_is_running():
    try:
        with urllib.request.urlopen(WEB_URL, timeout=1.5) as response:
            return 200 <= response.status < 500
    except Exception:
        return False


def make_icon(running):
    size = 64
    image = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)

    # Green means the DellPrintBridge web endpoint is responding. Gray means
    # the tray companion is running but the bridge itself cannot be reached.
    fill = (22, 140, 62, 255) if running else (110, 110, 110, 255)
    draw.rounded_rectangle((5, 5, 59, 59), radius=12, fill=fill)

    # Simple white printer glyph so no external icon asset is required.
    draw.rectangle((17, 12, 47, 28), fill=(255, 255, 255, 255))
    draw.rounded_rectangle((12, 24, 52, 45), radius=5, fill=(255, 255, 255, 255))
    draw.rectangle((18, 38, 46, 54), fill=fill)
    draw.rectangle((21, 41, 43, 51), fill=(255, 255, 255, 255))
    draw.ellipse((43, 29, 47, 33), fill=fill)

    return image


def open_web_console(icon=None, item=None):
    webbrowser.open(WEB_URL)


def stop_tray(icon, item=None):
    icon.stop()


def update_status(icon):
    global _bridge_running

    while icon.visible:
        running = bridge_is_running()
        with _state_lock:
            changed = running != _bridge_running
            _bridge_running = running

        if changed:
            icon.icon = make_icon(running)

        icon.title = (
            "DellPrintBridge - Running"
            if running
            else "DellPrintBridge - Bridge unavailable"
        )
        time.sleep(CHECK_INTERVAL_SECONDS)


def setup_icon(icon):
    global _bridge_running

    running = bridge_is_running()
    with _state_lock:
        _bridge_running = running

    icon.icon = make_icon(running)
    icon.title = (
        "DellPrintBridge - Running"
        if running
        else "DellPrintBridge - Bridge unavailable"
    )
    icon.visible = True

    threading.Thread(
        target=update_status,
        args=(icon,),
        daemon=True,
        name="DellPrintBridgeTrayStatus",
    ).start()


def main():
    running = bridge_is_running()
    menu = pystray.Menu(
        pystray.MenuItem(
            "Open DellPrintBridge",
            open_web_console,
            default=True,
        ),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Exit tray icon", stop_tray),
    )

    icon = pystray.Icon(
        "DellPrintBridge",
        make_icon(running),
        "DellPrintBridge",
        menu,
    )
    icon.run(setup=setup_icon)


if __name__ == "__main__":
    main()
