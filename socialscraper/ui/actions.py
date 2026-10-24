"""Aktionen der Lösungsknöpfe: ein einziger Ort für alles, was Übersicht, Banner, Probleme-Fenster und Hilfe auslösen.

Eine Aktion ist ein Wörterbuch `{'id': 'set_mode', 'label': …, …}` (siehe homelogic.attention). `Actions.run(action)` führt sie aus
und gibt True zurück, wenn etwas geschehen ist. Langsames (Dienst starten, Sicherung, Kanal ändern) läuft im Hintergrund-Thread.
Das Hauptfenster (`app`) braucht: select_tab, tabs, log, set_status, refresh_tabs und optional ui_state, show_problems.
"""
import pathlib
import tkinter as tk
from tkinter import messagebox

from .. import autologic as al
from .. import proxylogic
from . import common
from .common import TITLE
from .forms import friendly, run_async

HOME, FETCH, ACCOUNTS, DATA, AUTO, SETTINGS, HELP = ('Übersicht', 'Abrufen', 'Konten', 'Gespeicherte Daten', 'Automatik',
                                                     'Einstellungen', 'Hilfe')


class Actions:
    def __init__(self, app):
        self.app = app

    # ---------- Hilfen ----------

    def root(self):
        return getattr(self.app, 'root', None) or common.ROOT

    def db(self):
        return al.resolve_db(self.root())

    def tab(self, name):
        return getattr(self.app, 'tabs', {}).get(name)

    def say(self, text, kind='muted', log=True):
        self.app.set_status(text, kind)
        if log:
            self.app.log(text, {'ok': 'OK', 'warn': 'WARNING', 'err': 'ERROR'}.get(kind, ''))

    def state_update(self, key, **fields):
        state = getattr(self.app, 'ui_state', None)
        if state is not None:
            state.update_in(key, **fields)

    def refresh_home(self):
        home = self.tab(HOME)
        if home is not None:
            home.refresh()

    # ---------- Zentrale ----------

    def run(self, action):
        action = dict(action or {})
        handler = getattr(self, 'do_' + str(action.get('id', '')), None)
        if handler is None:
            return False
        return bool(handler(action))

    def goto(self, tab, sub=None):
        """Zu einem Tab springen und dort etwas markieren. `sub`: 'proxys' (Einstellungen), 'account:3', 'channel:7' oder None."""
        spec = {}
        if tab == SETTINGS and sub:
            spec = {'section': sub, 'field': {'proxys': 'proxy_mode', 'ki': 'ai_key'}.get(sub)}
        elif sub and ':' in str(sub):
            kind, _, number = str(sub).partition(':')
            try:
                spec = {f'{kind}_id': int(number)}
            except ValueError:
                spec = {}
        self.app.select_tab(tab)
        target = self.tab(tab)
        if target is not None and spec and hasattr(target, 'focus_item'):
            target.focus_item(spec)
        return True

    # ---------- Springen ----------

    def do_goto(self, a):
        return self.goto(a.get('tab'), a.get('sub'))

    def do_goto_fetch(self, a):
        self.app.select_tab(FETCH)
        tab = self.tab(FETCH)
        if tab is not None and hasattr(tab, 'focus_item'):
            tab.focus_item({'focus': 'links'})
        return True

    def do_goto_data(self, a):
        self.app.select_tab(DATA)
        self.state_update('home', viewed_data=True)
        self.refresh_home()
        return True

    def do_goto_accounts(self, a):
        return self.goto(ACCOUNTS)

    def do_goto_account(self, a):
        return self.goto(ACCOUNTS, f'account:{a["account_id"]}' if a.get('account_id') else None)

    def do_goto_channels(self, a):
        return self.goto(AUTO)

    def do_goto_channel(self, a):
        return self.goto(AUTO, f'channel:{a["target_id"]}' if a.get('target_id') else None)

    def do_goto_proxy(self, a):
        return self.goto(SETTINGS, 'proxys')

    def do_goto_ai(self, a):
        return self.goto(SETTINGS, 'ki')

    def do_goto_system(self, a):
        return self.goto(SETTINGS, 'system')

    def do_goto_help(self, a):
        self.app.select_tab(HELP)
        return True

    def do_add_channel(self, a):
        self.app.select_tab(AUTO)
        tab = self.tab(AUTO)
        if tab is not None:
            tab.add_channel()
        return True

    def do_mood(self, a):
        self.app.select_tab(DATA)
        tab = self.tab(DATA)
        if tab is not None:
            tab.mood_start()
        return True

    def do_open_problems(self, a):
        show = getattr(self.app, 'show_problems', None)
        if show is not None:
            show()
        return True

    def do_login(self, a):
        """Konto anmelden: zum Tab „Konten“, Konto wählen und die Anmeldung starten (mit den üblichen Prüfungen dort)."""
        self.goto(ACCOUNTS, f'account:{a["account_id"]}' if a.get('account_id') else None)
        tab = self.tab(ACCOUNTS)
        if tab is not None and tab.current() is not None:
            tab.login('qr')
        return True

    # ---------- Proxy-Modus ----------

    def do_set_mode(self, a):
        mode = a.get('value') or 'aus'
        old = a.get('from') or proxylogic.current_mode(self.root())
        if old == mode:
            return False
        old_title = proxylogic.MODE_TITLES.get(old, old)
        new_title = proxylogic.MODE_TITLES.get(mode, mode)
        text = (f'Der Proxy-Modus steht auf „{old_title}“. Dabei sind Konten gesperrt.\n\n'
                f'Auf „{new_title}“ stellen? Abrufe laufen dann über {"deine eigene Verbindung" if mode == "aus" else "die gewählte Verbindung"}, '
                'und Anmeldung und Konten gehen wieder. Das ist dieselbe Einstellung wie unter Einstellungen → Proxys.')
        if not messagebox.askyesno(TITLE, text):
            return False
        try:
            proxylogic.set_mode(self.root(), mode)
        except ValueError as error:
            self.app.log(f'Proxy-Modus nicht geändert: {error}', 'WARNING')
            messagebox.showwarning(TITLE, f'Der Proxy-Modus wurde nicht geändert.\n\n{error}')
            return False
        settings = self.tab(SETTINGS)
        if settings is not None and hasattr(settings, 'external_proxy_mode'):
            settings.external_proxy_mode(mode)
        self.say(f'Proxy-Modus auf „{new_title}“ gestellt. Konten gehen jetzt wieder.' if not proxylogic.is_anonymous(mode)
                 else f'Proxy-Modus auf „{new_title}“ gestellt.', 'ok')
        self.app.refresh_tabs()
        return True

    # ---------- Hintergrunddienst ----------

    def do_start_service(self, a):
        try:
            from .. import daemon
        except ImportError:
            messagebox.showinfo(TITLE, 'Der Hintergrunddienst fehlt in dieser Installation.')
            return False
        root = self.root()
        self.app.set_status('Der Hintergrunddienst startet …', 'warn')

        def done(_result, error):
            if error:
                self.say(f'Der Dienst konnte nicht starten: {friendly(error)}', 'err')
                messagebox.showerror(TITLE, friendly(error))
            else:
                self.say('Der Hintergrunddienst läuft jetzt.', 'ok')
            self.refresh_home()
        run_async(self.app, lambda: daemon.start_detached(root), done)
        return True

    def do_stop_service(self, a):
        try:
            from .. import daemon
        except ImportError:
            return False
        if not messagebox.askyesno(TITLE, 'Hintergrunddienst beenden?\n\nEin laufender Abruf bekommt bis zu 30 Sekunden, um sauber zu enden; '
                                          'danach wird er beendet und als unterbrochen vermerkt. '
                                          'Danach finden keine automatischen Abrufe mehr statt.'):
            return False
        root = self.root()
        self.app.set_status('Der Hintergrunddienst wird beendet … (kann bis zu einer halben Minute dauern)', 'warn')

        def done(result, error):
            if error or result is False:
                self.say('Der Dienst reagiert nicht und läuft noch.' if not error else friendly(error), 'err')
            else:
                self.say('Der Hintergrunddienst ist beendet.', 'ok')
            self.refresh_home()
        run_async(self.app, lambda: daemon.stop(root), done)
        return True

    # ---------- Sicherung ----------

    def do_backup(self, a):
        from .. import formlogic as fl
        from .. import repo
        src = self.db()
        folder = pathlib.Path(self.root()) / 'data' / 'backups'
        import datetime
        dest = folder / f'scrape-sicherung-{datetime.datetime.now():%Y-%m-%d_%H-%M}.db'
        self.app.set_status('Sicherungskopie wird angelegt …', 'warn')

        def work():
            folder.mkdir(parents=True, exist_ok=True)
            return fl.backup_database(src, dest)

        def done(result, error):
            if error:
                self.say(f'Die Sicherung hat nicht geklappt: {friendly(error)}', 'err')
                messagebox.showerror(TITLE, friendly(error))
            else:
                state = getattr(self.app, 'ui_state', None)
                if state is not None:
                    state.set('last_backup_at', repo.iso())
                self.say(f'Gesichert: {result["videos"]} Videos, {result["comments"]} Kommentare in {dest}', 'ok')
            self.refresh_home()
        run_async(self.app, work, done, daemon=False)   # Sicherung schreibt eine Datei: beim Schließen zu Ende laufen lassen
        return True

    # ---------- Kanäle ----------

    def _channel(self, name, a, ok_text):
        from .. import chanlogic
        db = self.db()
        target_id = a.get('target_id')

        def done(result, error):
            if error:
                self.say(friendly(error), 'err')
            else:
                self.say(result or ok_text, 'ok')
            self.app.refresh_tabs()
        run_async(self.app, lambda: chanlogic.action(db, target_id, name), done)
        return True

    def do_requeue_channel(self, a):
        return self._channel('requeue', a, 'Die fehlgeschlagenen Videos kommen noch einmal dran.')

    def do_resume_channel(self, a):
        return self._channel('resume', a, 'Der Kanal macht weiter.')

    # ---------- Erste Schritte ----------

    def do_skip_account(self, a):
        self.state_update('home', skip_account=True)
        self.refresh_home()
        return True

    def do_proxy_ok(self, a):
        self.state_update('home', proxy_ok=True)
        self.refresh_home()
        return True

    def do_hide_checklist(self, a):
        self.state_update('home', checklist_hidden=True)
        self.refresh_home()
        return True

    def do_show_checklist(self, a):
        self.state_update('home', checklist_hidden=False)
        self.refresh_home()
        return True
