"""Shared light desktop theme. No data, connection or job settings live here."""
from tkinter import ttk, font as tkfont

PALETTE = {
    'canvas': '#f3f6f8', 'surface': '#ffffff', 'soft': '#f7f9fb',
    'ink': '#193247', 'muted': '#617487', 'line': '#dce5eb',
    'accent': '#0f766e', 'accent_hover': '#115e59', 'accent_soft': '#e7f4f1',
    'sidebar': '#142b3b', 'nav_hover': '#213e50', 'nav_text': '#d8e4eb',
    'nav_muted': '#9eb3c1', 'ok': '#18745c', 'warn': '#9a620c', 'err': '#ba3745',
}
FONT = ('Segoe UI', 10)
FONT_B = ('Segoe UI', 10, 'bold')


def install(root):
    p = PALETTE
    style = ttk.Style(root)
    style.theme_use('clam')
    root.configure(background=p['canvas'])
    # A global *Font option would override named ttk title/metric styles.
    for name in ('TkDefaultFont', 'TkTextFont', 'TkMenuFont', 'TkHeadingFont'):
        tkfont.nametofont(name, root=root).configure(family='Segoe UI', size=10)
    for pattern, value in {
        '*Toplevel.background': p['surface'], '*Text.background': p['surface'],
        '*Text.foreground': p['ink'], '*Text.insertBackground': p['ink'],
        '*Text.selectBackground': p['accent_soft'], '*Text.selectForeground': p['ink'],
        '*Text.highlightColor': p['accent'], '*Text.highlightBackground': p['line'],
        '*Text.highlightThickness': 1, '*Text.relief': 'flat',
        '*Text.padX': 9, '*Text.padY': 7, '*Listbox.background': p['surface'],
        '*Listbox.foreground': p['ink'], '*Listbox.selectBackground': p['accent_soft'],
        '*Listbox.selectForeground': p['accent_hover'], '*Listbox.relief': 'flat',
        '*Menu.background': p['surface'], '*Menu.foreground': p['ink'],
        '*Menu.activeBackground': p['accent_soft'], '*Menu.activeForeground': p['ink'],
    }.items():
        root.option_add(pattern, value)
    style.configure('.', font=FONT, background=p['surface'], foreground=p['ink'],
                    bordercolor=p['line'], lightcolor=p['surface'], darkcolor=p['line'])
    style.configure('TFrame', background=p['surface'])
    style.configure('Canvas.TFrame', background=p['canvas'])
    style.configure('Soft.TFrame', background=p['soft'])
    style.configure('Card.TFrame', background=p['surface'], borderwidth=1, relief='solid')
    style.configure('TLabel', background=p['surface'], foreground=p['ink'])
    style.configure('Muted.TLabel', foreground=p['muted'])
    style.configure('Title.TLabel', font=('Segoe UI', 19, 'bold'), background=p['canvas'])
    style.configure('PageHint.TLabel', foreground=p['muted'], background=p['canvas'])
    style.configure('Eyebrow.TLabel', font=('Segoe UI', 9), foreground=p['muted'], background=p['canvas'])
    style.configure('Section.TLabel', font=('Segoe UI', 12, 'bold'))
    style.configure('Metric.TLabel', font=('Segoe UI', 23, 'bold'))
    style.configure('Hint.TLabel', background=p['accent_soft'], foreground=p['accent_hover'])
    style.configure('Hint.TFrame', background=p['accent_soft'])
    style.configure('Status.TFrame', background=p['canvas'])
    style.configure('Status.TLabel', background=p['canvas'], foreground=p['muted'])
    style.configure('Badge.TLabel', padding=(10, 5), background=p['accent_soft'],
                    foreground=p['accent_hover'], font=('Segoe UI', 9, 'bold'))
    style.configure('TButton', font=FONT, padding=(9, 4), background=p['soft'],
                    foreground=p['ink'], borderwidth=1, relief='flat',
                    focusthickness=1, focuscolor=p['accent'])
    style.map('TButton', background=[('disabled', '#f1f4f6'), ('pressed', '#dcebe8'), ('active', '#eaf0f4')],
              foreground=[('disabled', '#8797a3')], bordercolor=[('focus', p['accent'])])
    for name in ('Primary.TButton', 'Big.TButton'):
        style.configure(name, background=p['accent'], foreground='white', bordercolor=p['accent'],
                        font=FONT_B, padding=(11, 5))
        style.map(name, background=[('disabled', '#dde7e5'), ('pressed', '#134e4a'),
                                    ('active', p['accent_hover'])],
                  foreground=[('disabled', '#798d89'), ('!disabled', 'white')],
                  bordercolor=[('disabled', '#dde7e5'), ('!disabled', p['accent'])])
    style.configure('Danger.TButton', foreground=p['err'])
    style.map('Danger.TButton', foreground=[('disabled', '#8797a3'), ('!disabled', p['err'])])
    style.configure('Quiet.TButton', background=p['surface'], bordercolor=p['surface'])
    for name in ('TEntry', 'TCombobox', 'TSpinbox'):
        style.configure(name, padding=(6, 4), fieldbackground=p['surface'],
                        bordercolor=p['line'], foreground=p['ink'], arrowsize=13)
        style.map(name, bordercolor=[('focus', p['accent']), ('!focus', p['line'])],
                  fieldbackground=[('readonly', p['soft']), ('disabled', '#f1f4f6')],
                  foreground=[('disabled', '#8797a3')])
    for name in ('TCheckbutton', 'TRadiobutton'):
        style.configure(name, padding=(0, 3), background=p['surface'],
                        indicatorbackground=p['surface'], indicatorforeground=p['accent'])
        style.map(name, background=[('active', p['surface'])],
                  foreground=[('disabled', '#8797a3')], indicatorbackground=[('selected', p['accent_soft'])])
    style.configure('TLabelframe', background=p['surface'], bordercolor=p['line'], borderwidth=1, relief='solid')
    style.configure('TLabelframe.Label', font=FONT_B, foreground=p['ink'], background=p['surface'])
    style.configure('Treeview', font=FONT, rowheight=28, background=p['surface'],
                    fieldbackground=p['surface'], foreground=p['ink'], borderwidth=0)
    style.configure('Treeview.Heading', font=('Segoe UI', 9, 'bold'), padding=(8, 6),
                    background=p['soft'], foreground=p['muted'], relief='flat')
    style.map('Treeview', background=[('selected', '#e0f0ed')], foreground=[('selected', '#104f49')])
    style.map('Treeview.Heading', background=[('active', '#eaf0f4')])
    style.configure('Compact.Treeview', rowheight=27)
    style.configure('TMenubutton', font=FONT, padding=(9, 4), background=p['soft'],
                    foreground=p['ink'], bordercolor=p['line'], relief='flat')
    style.map('TMenubutton', background=[('active', '#eaf0f4')], foreground=[('disabled', '#8797a3')])
    style.configure('TNotebook', background=p['surface'], borderwidth=0, tabmargins=(0, 0, 0, 6))
    style.configure('TNotebook.Tab', padding=(13, 7), background=p['soft'])
    style.map('TNotebook.Tab', background=[('selected', p['accent_soft']), ('active', '#edf2f5')],
              foreground=[('selected', p['accent_hover'])])
    style.configure('Workspace.TNotebook', background=p['surface'], borderwidth=0)
    style.layout('Workspace.TNotebook.Tab', [])
    style.configure('TSeparator', background=p['line'])
    for orient in ('Vertical', 'Horizontal'):
        style.configure(orient + '.TScrollbar', arrowsize=12, width=12, borderwidth=0,
                        background='#c3d0d9', troughcolor=p['soft'], arrowcolor=p['muted'])
    style.configure('Nav.Vertical.TScrollbar', background=p['nav_hover'], troughcolor=p['sidebar'],
                    arrowcolor=p['nav_muted'], bordercolor=p['sidebar'])
    style.configure('Horizontal.TProgressbar', background=p['accent'], troughcolor='#e5ecef',
                    borderwidth=0, thickness=4)
    return style
