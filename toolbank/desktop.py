"""Windows desktop automation. pywinauto for real controls (stable); pyautogui for Citrix/pixels (fragile, last resort).
pip install pywinauto pyautogui pyperclip opencv-python"""
import time
from pathlib import Path

from .robust import SystemError_


# ---------- pywinauto ----------

def app_connect(title_re=None, exe=None, backend="uia"):
    """Attach to a running window by title regex, or start an exe. Returns pywinauto Application."""
    from pywinauto import Application
    app = Application(backend=backend)
    return app.connect(title_re=title_re, timeout=20) if title_re else app.start(exe)


def win(app, title_re, timeout=30):
    """Top-level window, waited until ready."""
    w = app.window(title_re=title_re)
    w.wait("ready", timeout=timeout)
    return w


def dump_controls(window, depth=3):
    """Print the control tree — how you find title / auto_id / control_type for the locators below."""
    window.print_control_identifiers(depth=depth)


def ctl_click(window, timeout=15, **locator):
    """Click a control: ctl_click(w, title='Save', control_type='Button') or auto_id='btnOK'."""
    window.child_window(**locator).wait("enabled", timeout=timeout).click_input()


def ctl_type(window, text, timeout=15, **locator):
    """Clear a field and type text into it."""
    c = window.child_window(**locator).wait("enabled", timeout=timeout)
    try:
        c.set_edit_text("")
    except Exception:
        pass
    c.type_keys(text, with_spaces=True, set_foreground=True)


def ctl_text(window, timeout=15, **locator):
    """Read a control's text/value."""
    c = window.child_window(**locator).wait("exists", timeout=timeout)
    txt = c.window_text()
    if not txt and hasattr(c, "get_value"):
        txt = c.get_value()
    return txt


def ctl_select(window, item, timeout=15, **locator):
    """Pick an item in a combo/list box."""
    window.child_window(**locator).wait("enabled", timeout=timeout).select(item)


def wait_for_window(app, title_re, timeout=60):
    """Wait for a dialog/popup to appear; returns it."""
    w = app.window(title_re=title_re)
    w.wait("visible", timeout=timeout)
    return w


# ---------- pyautogui (pixels) ----------

def screen_find(image_path, confidence=0.9, timeout=30):
    """Centre (x, y) of an on-screen image (saved screenshot of a button); raises if not found."""
    import pyautogui
    end = time.time() + timeout
    while time.time() < end:
        try:
            box = pyautogui.locateCenterOnScreen(str(image_path), confidence=confidence)
        except Exception:   # newer pyautogui raises ImageNotFoundException
            box = None
        if box:
            return box
        time.sleep(1)
    raise SystemError_(f"image not found on screen: {Path(image_path).name}", code="SCREEN_FIND")


def screen_click(image_path, **kw):
    """Click the centre of an on-screen image."""
    import pyautogui
    x, y = screen_find(image_path, **kw)
    pyautogui.click(x, y)


def screen_type(text, interval=0.02):
    """Type into whatever has focus (ASCII; use clipboard_paste for long/Unicode text)."""
    import pyautogui
    pyautogui.write(text, interval=interval)


def hotkey(*keys):
    """hotkey('ctrl', 's'), hotkey('alt', 'f4')."""
    import pyautogui
    pyautogui.hotkey(*keys)


def clipboard_paste(text):
    """Put text on the clipboard and Ctrl+V."""
    import pyautogui
    import pyperclip
    pyperclip.copy(text)
    pyautogui.hotkey("ctrl", "v")


def clipboard_read():
    """Current clipboard text (e.g. after Ctrl+A, Ctrl+C on a Citrix grid)."""
    import pyperclip
    return pyperclip.paste()


def screen_capture(path):
    """Screenshot of the whole screen."""
    import pyautogui
    pyautogui.screenshot(str(path))
    return Path(path)
