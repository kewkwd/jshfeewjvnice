"""Shared create-group action for channel and target selectors."""
from tkinter import messagebox, simpledialog

from .. import autologic
from .common import TITLE


def create_selected_group(dialog, *, no_group, new_group):
    if dialog.group_var.get() != new_group:
        return
    name = simpledialog.askstring(TITLE, 'Name der neuen Gruppe:', parent=dialog)
    if not name or not name.strip():
        dialog.group_var.set(no_group)
        return
    try:
        group = autologic.create_group(dialog.db_path, name)
    except ValueError as error:
        messagebox.showwarning(TITLE, str(error), parent=dialog)
        dialog.group_var.set(no_group)
        return
    dialog.groups.append(group)
    dialog.group_cb.configure(values=[no_group] + [g['name'] for g in dialog.groups] + [new_group])
    dialog.group_var.set(group['name'])
