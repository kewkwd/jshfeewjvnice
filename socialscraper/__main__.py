import os
if os.name == "nt":
    # Windows soll bei einer blockierten Programmdatei kein Fehlfenster öffnen (und den Lauf anhalten);
    # der Fehler kommt stattdessen als normale Python-Ausnahme an. Wird an Kindprozesse vererbt.
    import ctypes
    ctypes.windll.kernel32.SetErrorMode(0x0001 | 0x8000)  # SEM_FAILCRITICALERRORS | SEM_NOOPENFILEERRORBOX
from .cli import main
import sys
sys.exit(main())
