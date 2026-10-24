"""Socialscraper – Desktop-Programm (eigenes Fenster, kein Browser nötig).

Dünner Einstieg: `python -m socialscraper.gui`. Das Fenster selbst steht im Paket `socialscraper.ui`.
"""
import os
import sys
import tkinter as tk
from tkinter import messagebox

from .ui.app import App, TABS  # noqa: F401 – Re-Export
from .ui.common import COLORS, FONT, FONT_B, GUI_LOG, ICON, ROOT, TITLE, system_check  # noqa: F401 – Re-Exports

from .branding import APP_ID


def _windows_setup():
    """DPI-scharf und eigene Taskleisten-Identität (nicht unter „Python“ gruppiert)."""
    if os.name != 'nt':
        return
    import ctypes
    try: ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception: pass
    try: ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_ID)
    except Exception: pass
    try: ctypes.windll.kernel32.SetErrorMode(0x0001 | 0x8000)  # keine Windows-Fehlfenster bei blockierten Dateien
    except Exception: pass


def _single_instance():
    """True, wenn dies die einzige laufende Instanz ist (zwei Fenster würden um Browserprofile streiten)."""
    if os.name != 'nt':
        return True
    import ctypes
    from ctypes import wintypes
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.CreateMutexW.argtypes = [wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR]
    kernel.CreateMutexW.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.restype = wintypes.BOOL
    ctypes.set_last_error(0)
    handle = kernel.CreateMutexW(None, False, 'Local\\' + APP_ID)
    error = ctypes.get_last_error()
    if not handle:
        raise ctypes.WinError(error)
    if error == 183:  # ERROR_ALREADY_EXISTS
        kernel.CloseHandle(handle)
        return False
    _single_instance.handle = handle  # offen halten, solange der Prozess lebt
    return True



def main():
    os.chdir(ROOT)  # relative Pfade in settings.toml gelten ab dem Programmordner
    _windows_setup()
    try:
        if not _single_instance():
            root = tk.Tk(); root.withdraw()
            try:
                messagebox.showinfo(TITLE, f'{TITLE} läuft bereits.')
            finally:
                root.destroy()
            return 0
        network=os.environ.get('SS_AUDIT_NETWORK')
        if network in ('proxy','offline'):
            from .config import load
            from .net.testpolicy import enable
            enable(load(),offline=network=='offline')
        App().mainloop()
    except Exception as error:  # nie lautlos sterben: unter pythonw gäbe es sonst keine Meldung
        root = tk.Tk(); root.withdraw()
        messagebox.showerror(TITLE, f'Unerwarteter Fehler: {error}')
        raise
    return 0


if __name__ == '__main__':
    sys.exit(main())
