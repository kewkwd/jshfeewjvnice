"""A separate offline sandbox for the Desktop HTML generator."""
from pathlib import Path
import tkinter as tk
from tkinter import ttk, filedialog

from .. import local_generator
from .widgets import run_in_thread


def show(parent, cfg, identity=None):
    window = tk.Toplevel(parent)
    window.title('Datengenerator – nur lokale Tests')
    window.geometry('740x680')
    frame = ttk.Frame(window, padding=16)
    frame.pack(fill='both', expand=True)
    ttk.Label(frame, text='Lokale Testdaten', font=('Segoe UI', 16, 'bold')).pack(anchor='w')
    ttk.Label(frame, text='Getrennt von echten Profil- und Registrierungsdaten. Keine Netzwerkverbindung, '
              'kein Start von Browser oder Android.', wraplength=680).pack(fill='x', pady=8)
    source = tk.StringVar(value=str(Path.home() / 'Desktop' / 'Datengenerator.html'))
    row = ttk.Frame(frame)
    row.pack(fill='x')
    entry = ttk.Entry(row, textvariable=source)
    entry.pack(side='left', fill='x', expand=True)

    def choose():
        path = filedialog.askopenfilename(parent=window, title='Lokalen Datengenerator auswählen',
                                          filetypes=[('HTML-Datei', '*.html *.htm')])
        if path:
            source.set(path)

    browse = ttk.Button(row, text='Datei wählen', command=choose)
    browse.pack(side='left', padx=8)
    modes = (['Gespeicherte Sticky-Messung: ' + identity] if identity else []) + ['Lokale Simulation (ohne Proxy)']
    mode = tk.StringVar(value=modes[0])
    selector = ttk.Combobox(frame, values=modes, textvariable=mode, state='readonly')
    selector.pack(fill='x', pady=8)
    city = tk.StringVar(value='')
    ttk.Label(frame, text='Stadt (nur für die lokale Simulation):').pack(anchor='w')
    city_entry = ttk.Entry(frame, textvariable=city)
    city_entry.pack(fill='x', pady=4)
    report = tk.Text(frame, wrap='word', state='disabled', padx=8, pady=8)

    def display(text):
        report.configure(state='normal')
        report.delete('1.0', 'end')
        report.insert('1.0', text)
        report.configure(state='disabled')

    def run():
        use_snapshot = identity is not None and mode.get() == modes[0]
        options = {'identity': identity} if use_snapshot else {'simulated_city': city.get()}
        path = source.get()
        for widget in (entry, browse, selector, city_entry, create):
            widget.configure(state='disabled')
        display('Lokale Datenblöcke werden gelesen und mit der Ortsreferenz verglichen …')

        def work():
            try:
                return local_generator.create(cfg, path, **options), None
            except (ValueError, OSError) as error:
                return None, str(error)

        def done(value, error):
            for widget in (entry, browse, city_entry, create):
                widget.configure(state='normal')
            selector.configure(state='readonly')
            if error:
                display('Lokale Erstellung konnte nicht abgeschlossen werden. Keine Profildaten geändert.')
            elif value[1]:
                display(value[1])
            else:
                display(local_generator.summary(*value[0]))

        try:
            run_in_thread(window, work, done)
        except Exception:
            done(None, True)

    create = ttk.Button(frame, text='Lokalen Testdatensatz erstellen', command=run)
    create.pack(anchor='w', pady=8)
    report.pack(fill='both', expand=True)
    display('Eine Profilzeile vor dem Öffnen auswählen, um deren gespeicherte Sticky-Messung zu verwenden. '
            'Ist keine Messung vorhanden, kann eine ausdrücklich markierte Simulation erstellt werden. '
            'Eine alte Messung belegt keine aktuelle Proxy-IP.')
    return window
