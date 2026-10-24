"""Dialog „Fester Proxy für ein Konto“: Eingabe mit Formatprüfung, „Testen“ (Ausgangs-IP), Speichern, Entfernen.

Das Passwort steht nur im Eingabefeld dieses Dialogs (maskiert) und wird nirgends angezeigt: der bereits gespeicherte
Proxy erscheint nur ohne Passwort. Das Testen läuft in einem Hintergrund-Thread, nie im Fenster-Thread.
"""
import tkinter as tk
from tkinter import ttk

from . import logic
from .common import COLORS
from .widgets import run_in_thread, show_centered


class ProxyDialog(tk.Toplevel):
    """`run()` gibt ('save', Adresse), ('remove', None) oder None zurück.

    `tester(adresse_oder_None)` -> (ok, Meldung, IP); mit None wird der gespeicherte Proxy getestet. Standard: logic.test_proxy."""

    EXAMPLE = 'Beispiel: http://benutzer:passwort@1.2.3.4:8080 (erlaubt: http, https, socks5, socks4)'

    def __init__(self, parent, account_name, current=None, tester=None, platform_hint=''):
        super().__init__(parent)
        self.withdraw()
        self.title('Fester Proxy')
        self.resizable(False, False)
        self.result, self.current, self.busy = None, current, False
        self.tester = tester or (lambda raw: logic.test_proxy(raw))
        body = ttk.Frame(self, padding=14)
        body.grid(row=0, column=0, sticky='nsew')
        body.columnconfigure(0, weight=1)
        ttk.Label(body, text=f'Fester Proxy für {account_name}', font=('Segoe UI', 10, 'bold'), wraplength=440,
                  justify='left').grid(row=0, column=0, sticky='w')
        ttk.Label(body, text='Dieses Konto benutzt dann immer nur diesen Proxy – nie deine normale Verbindung und keinen '
                             'anderen Proxy. Ist er nicht erreichbar, wird nichts abgerufen und das Konto pausiert kurz.'
                             + (' ' + platform_hint if platform_hint else ''),
                  wraplength=440, justify='left').grid(row=1, column=0, sticky='w', pady=(6, 8))
        self.current_label = ttk.Label(body, text='', wraplength=440, justify='left', foreground=COLORS['muted'])
        self.current_label.grid(row=2, column=0, sticky='w')
        self.current_label.configure(text=f'Jetzt festgelegt: {current}' if current else 'Noch kein fester Proxy festgelegt.')
        ttk.Label(body, text='Neue Proxy-Adresse' if current else 'Proxy-Adresse').grid(row=3, column=0, sticky='w', pady=(8, 2))
        row = ttk.Frame(body)
        row.grid(row=4, column=0, sticky='ew')
        row.columnconfigure(0, weight=1)
        self.var = tk.StringVar()
        self.entry = ttk.Entry(row, textvariable=self.var, width=46, show='•')
        self.entry.grid(row=0, column=0, sticky='ew')
        self.shown = False
        self.show_btn = ttk.Button(row, text='Anzeigen', command=self.toggle_show)
        self.show_btn.grid(row=0, column=1, padx=(8, 0))
        ttk.Label(body, text=self.EXAMPLE + '. Das Passwort wird nur in der Datei data/account_proxies.json gespeichert '
                             '(nicht in der Datenbank) und nie angezeigt.' + (' Leer lassen und „Testen“ prüft den gespeicherten Proxy.'
                                                                              if current else ''),
                  foreground=COLORS['muted'], wraplength=440, justify='left').grid(row=5, column=0, sticky='w', pady=(2, 0))
        self.message = ttk.Label(body, text='', wraplength=440, justify='left')
        self.message.grid(row=6, column=0, sticky='w', pady=(8, 0))
        buttons = ttk.Frame(body)
        buttons.grid(row=7, column=0, sticky='e', pady=(12, 0))
        self.test_btn = ttk.Button(buttons, text='Testen', command=self.test)
        self.test_btn.pack(side='left', padx=(0, 8))
        self.save_btn = ttk.Button(buttons, text='Speichern', style='Big.TButton', command=self.save)
        self.save_btn.pack(side='left', padx=(0, 8))
        self.remove_btn = ttk.Button(buttons, text='Entfernen', command=self.remove)
        self.remove_btn.pack(side='left', padx=(0, 8))
        if not current:
            self.remove_btn.configure(state='disabled')
        ttk.Button(buttons, text='Abbrechen', command=self.cancel).pack(side='left')
        self.bind('<Escape>', self.cancel)
        self.protocol('WM_DELETE_WINDOW', self.cancel)

    # ---------- Bedienung ----------

    def toggle_show(self):
        self.shown = not self.shown
        self.entry.configure(show='' if self.shown else '•')
        self.show_btn.configure(text='Verbergen' if self.shown else 'Anzeigen')

    def _say(self, text, kind='muted'):
        self.message.configure(text=text, foreground=COLORS[kind])

    def _set_busy(self, busy):
        self.busy = busy
        for button in (self.test_btn, self.save_btn, self.remove_btn):
            button.configure(state='disabled' if busy else 'normal')
        if not busy and not self.current:
            self.remove_btn.configure(state='disabled')

    def _checked(self):
        """Eingabe prüfen: (Adresse, Fehler). Leer = (None, None), wenn es schon einen gespeicherten Proxy gibt."""
        raw = self.var.get().strip()
        if not raw:
            if self.current:
                return None, None
            return None, 'Bitte eine Proxy-Adresse eingeben. ' + self.EXAMPLE
        shown, error = logic.check_proxy_text(raw)
        return (raw, None) if error is None else (None, error)

    def test(self):
        if self.busy:
            return
        raw, error = self._checked()
        if error:
            self._say(error, 'err')
            return
        self._say('Teste den Proxy … (das kann einige Sekunden dauern)', 'muted')
        self._set_busy(True)

        def done(value, failure):
            if not self.winfo_exists():
                return
            self._set_busy(False)
            if failure is not None:
                self._say('Der Test hat nicht geklappt: ' + logic.friendly_error(failure), 'err')
                return
            ok, text, _ip = value
            self._say(('✓ ' if ok else '✗ ') + text, 'ok' if ok else 'err')
        run_in_thread(self, lambda: self.tester(raw), done)

    def save(self):
        if self.busy:
            return
        raw, error = self._checked()
        if error:
            self._say(error, 'err')
            return
        if raw is None:
            self._say('Es wurde nichts eingegeben. Zum Ändern eine neue Adresse eintragen.', 'warn')
            return
        self.result = ('save', raw)
        self.destroy()

    def remove(self):
        if self.busy or not self.current:
            return
        self.result = ('remove', None)
        self.destroy()

    def cancel(self, _event=None):
        self.result = None
        self.destroy()

    def run(self):
        parent = self.master
        self.update_idletasks()
        try:
            self.transient(parent.winfo_toplevel())
            show_centered(self, parent)
            self.grab_set()
        except tk.TclError:
            pass
        self.entry.focus_set()
        self.wait_window()
        return self.result
