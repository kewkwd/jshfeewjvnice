"""Zusätzliche Kommandozeilenbefehle: jede Datei hier mit NAME, add_arguments(parser) und run(args, cfg) wird automatisch angemeldet.

run(args, cfg) gibt den Exit-Code zurück. Die Konfiguration ist schon geladen und geprüft, die Protokollierung eingerichtet.
"""
import importlib
import pkgutil

REGISTRY = {}


def register_all(sub):
    for info in pkgutil.iter_modules(__path__):
        module = importlib.import_module(f'{__name__}.{info.name}')
        # Pro Aufruf anmelden (nicht nur beim ersten): sonst kennt ein zweiter parser() die Befehle nicht.
        if getattr(module, 'NAME', None) and module.NAME not in sub.choices:
            parser = sub.add_parser(module.NAME, help=getattr(module, 'HELP', None))
            module.add_arguments(parser)
            REGISTRY[module.NAME] = module
