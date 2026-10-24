"""Local settings and manual IPQS checks, without a local HTTP service."""
import tkinter as tk
from tkinter import ttk, messagebox

from ..net import ipqs
from .widgets import run_in_thread


def show(parent, cfg, identity=None):
    window = tk.Toplevel(parent)
    window.title('IPQS – Proxy-Bewertung')
    window.geometry('700x650')
    window.minsize(480, 500)
    frame = ttk.Frame(window, padding=16)
    frame.pack(fill='both', expand=True)
    ttk.Label(frame, text='Proxy mit IPQualityScore prüfen', font=('Segoe UI', 15, 'bold')).pack(anchor='w')
    ttk.Label(frame, text='Nur auf deinen Klick. IPQS erhält die gemessene Proxy-IP und deinen API-Schlüssel; '
              'keine Namen, Anschriften oder Proxy-Passwörter. Kein offener Port auf deinem PC.',
              wraplength=620, justify='left').pack(fill='x', pady=8)
    key_status = ttk.Label(frame)
    key_status.pack(anchor='w')
    key = tk.StringVar()
    key_row = ttk.Frame(frame)
    key_row.pack(fill='x', pady=6)
    ttk.Label(key_row, text='API-Schlüssel:').pack(side='left')
    entry = ttk.Entry(key_row, textvariable=key, show='*')
    entry.pack(side='left', fill='x', expand=True, padx=8)
    buttons = ttk.Frame(frame)
    buttons.pack(fill='x')

    def status():
        key_status.configure(text='Schlüssel lokal hinterlegt (Windows-Benutzerschutz).' if ipqs.has_key(cfg)
                             else 'Noch kein IPQS-Schlüssel hinterlegt.')

    def save():
        try:
            ipqs.save_key(cfg, key.get())
            key.set('')
            status()
        except (ValueError, OSError):
            messagebox.showerror('IPQS-Schlüssel', 'Schlüssel konnte nicht sicher gespeichert werden. '
                                 'Bitte einen gültigen IPQS-Schlüssel eingeben und Dateizugriff prüfen.', parent=window)

    def remove():
        try:
            ipqs.delete_key(cfg)
            key.set('')
            status()
        except OSError:
            messagebox.showerror('IPQS-Schlüssel', 'Der Schlüssel konnte nicht gelöscht werden.', parent=window)

    save_button = ttk.Button(buttons, text='Schlüssel geschützt speichern', command=save)
    save_button.pack(side='left')
    remove_button = ttk.Button(buttons, text='Schlüssel löschen', command=remove)
    remove_button.pack(side='left', padx=8)
    choices = ([('Profil: ' + identity, identity)] if identity else []) + [('Proxy aus Einstellungen', None)]
    target = tk.StringVar(value=choices[0][0])
    ttk.Label(frame, text='Zu prüfende Verbindung:').pack(anchor='w', pady=(12, 0))
    selector = ttk.Combobox(frame, textvariable=target, values=[label for label, _ in choices], state='readonly')
    selector.pack(fill='x', pady=6)
    report = tk.Text(frame, wrap='word', height=13, padx=8, pady=8, state='disabled')

    def display(text):
        report.configure(state='normal')
        report.delete('1.0', 'end')
        report.insert('1.0', text)
        report.configure(state='disabled')

    def selected_identity():
        return dict(choices)[target.get()]

    def previous(event=None):
        display(ipqs.summary(ipqs.load(cfg, selected_identity())))

    def check():
        if not ipqs.has_key(cfg):
            display('Bitte zuerst einen IPQS-Schlüssel eingeben und geschützt speichern.')
            return
        if key.get():
            display('Im Schlüsselfeld steht eine ungespeicherte Eingabe. Bitte zuerst speichern oder das Feld leeren.')
            return
        ident = selected_identity()
        for widget in (entry, save_button, remove_button, check_button, selector):
            widget.configure(state='disabled')
        display('Ausgangs-IP wird gemessen und einmalig über IPQS bewertet …')

        def done(result, error):
            if not window.winfo_exists():
                return
            for widget in (entry, save_button, remove_button, check_button):
                widget.configure(state='normal')
            selector.configure(state='readonly')
            display('Die Prüfung konnte nicht abgeschlossen werden. Nicht bewertet.' if error else ipqs.summary(result))

        try:
            run_in_thread(window, lambda: ipqs.check(cfg, ident), done)
        except Exception:
            done(None, True)

    check_button = ttk.Button(frame, text='Jetzt IPQS-Prüfung durchführen', command=check)
    check_button.pack(anchor='w', pady=(0, 8))
    report.pack(fill='both', expand=True)
    selector.bind('<<ComboboxSelected>>', previous)
    ttk.Label(frame, text='Niedriger ist besser (0–100). Ab 75 auffällig, ab 90 hohes Risiko laut IPQS. '
              'Fehler zählen nie als guter Score. Kein automatischer Wechsel oder Registrierungsversuch.',
              wraplength=620, justify='left').pack(fill='x', pady=8)
    status()
    previous()
    return window
