import datetime, queue, threading

class Auftrag:
    def __init__(self, widget, funktion, *, bei_zeile=None, bei_ende=None, takt=100):
        self.widget, self.funktion = widget, funktion
        self.bei_zeile, self.bei_ende, self.takt = bei_zeile, bei_ende, takt
        self._schlange, self._stop, self.laeuft = queue.Queue(), threading.Event(), False

    def stop(self):
        return self._stop.is_set()

    def melden(self, text):
        self._schlange.put(('zeile', str(text)))

    def starten(self):
        if self.laeuft:
            return False
        from tkinter import messagebox
        app = getattr(self.widget, 'app', None)
        if app and (app.job is not None or any(
                getattr(getattr(tab, '_auftrag', None), 'laeuft', False)
                for tab in app.tabs.values())):
            messagebox.showinfo('Auftrag läuft', 'Bitte warte, bis der laufende Auftrag beendet ist.')
            return False
        self.laeuft = True
        self._stop.clear()
        if app:
            app._busy(True)
        try:
            threading.Thread(target=self._lauf, daemon=True).start()
            self.widget.after(self.takt, self._abholen)
        except Exception:
            self.laeuft = False
            if app:app._busy(False)
            raise
        return True

    def abbrechen(self):
        self._stop.set()

    def _lauf(self):
        try:
            self._schlange.put(('ende', self.funktion(self.melden, self.stop), None))
        except Exception as fehler:
            self._schlange.put(('ende', None, fehler))

    def _abholen(self):
        try:
            while True:
                art, *rest = self._schlange.get_nowait()
                if art == 'zeile':
                    if self.bei_zeile:
                        try:
                            self.bei_zeile(rest[0])
                        except Exception as error:
                            app = getattr(self.widget, 'app', None)
                            if app:app.log(f'Detailanzeige fehlgeschlagen: {error}','ERROR')
                else:
                    self.laeuft = False
                    app = getattr(self.widget, 'app', None)
                    if app:
                        app._busy(False)
                    if self.bei_ende:
                        self.bei_ende(rest[0], rest[1])
                    return
        except queue.Empty:
            pass
        if self.laeuft:
            self.widget.after(self.takt, self._abholen)

class Protokoll:
    MAX_ZEILEN, KUERZEN_UM = 3000, 800   # wie App.log: ab 3000 Zeilen die ältesten 800 löschen, sonst wächst das Feld endlos

    def __init__(self, text_widget, hoehe=8):
        from .plain_log import PlainLog
        self._plain_log = PlainLog()
        self.text = text_widget
        self.text.configure(state='disabled', height=hoehe, wrap='word')

    def anhaengen(self, zeile):
        from . import common
        raw = str(zeile)
        app = self.text.winfo_toplevel()
        writer = getattr(app, '_write_log', None)
        if callable(writer):
            writer(common.GUI_LOG.with_name('gui-details.log'), raw)
        shown, tag = self._plain_log.format(raw)
        if shown is None:
            return
        category = {'ERROR': 'Problem: ', 'CRITICAL': 'Problem: ', 'WARNING': 'Hinweis: ', 'OK': 'Erledigt: '}.get(tag, '')
        if shown.startswith(('Fertig:', 'Erledigt:', 'Hinweis:', 'Deine Hilfe', 'Problem:')):
            category = ''
        stamp = datetime.datetime.now().strftime('%H:%M:%S')
        self.text.configure(state='normal')
        self.text.insert('end', f'{stamp} · {category}{shown}\n')
        if int(self.text.index('end-1c').split('.')[0]) > self.MAX_ZEILEN:
            self.text.delete('1.0', f'{self.KUERZEN_UM}.0')
        self.text.see('end')
        self.text.configure(state='disabled')

    def leeren(self):
        self._plain_log.reset()
        self.text.configure(state='normal')
        self.text.delete('1.0', 'end')
        self.text.configure(state='disabled')

def cfg_of(app):
    cfg = getattr(app, 'cfg', None)
    if cfg is not None:
        return cfg
    from .. import guicore
    return guicore.read_settings(guicore.ROOT)
