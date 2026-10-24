"""Befehl `daemon`: der Hintergrunddienst (arbeitet fällige Ziele der Automatik ab)."""
import logging
import logging.handlers
import os
import pathlib
import sys

NAME = 'daemon'
HELP = 'Hintergrunddienst: fällige automatische Abrufe nacheinander ausführen'


def add_arguments(p):
    p.add_argument('--once', action='store_true', help='einmal alle fälligen Ziele abarbeiten und beenden')
    p.add_argument('--poll', type=int, default=30, help='Sekunden zwischen zwei Prüfungen (Standard 30)')
    p.add_argument('--root', default=None, help='Projektordner (Standard: aktueller Ordner)')


def _setup_file_log(root):
    path = pathlib.Path(root) / 'data' / 'daemon.log'
    path.parent.mkdir(parents=True, exist_ok=True)
    handler = logging.handlers.RotatingFileHandler(path, maxBytes=1_000_000, backupCount=3, encoding='utf-8')
    handler.setFormatter(logging.Formatter('%(asctime)s %(levelname)-7s %(name)s | %(message)s'))
    logger = logging.getLogger()
    if sys.stderr is None:  # pythonw: kein Konsolenfenster
        for h in list(logger.handlers):
            logger.removeHandler(h)
    logger.addHandler(handler)
    from ..net.accountproxy import install_log_filter
    install_log_filter()   # Proxy-Passwörter auch in daemon.log unkenntlich (unter pythonw ist das der einzige Kanal)
    return handler


def log_failure(args, error):
    """Fehler, die vor `run` auftreten (kaputte settings.toml, unbekannter Schlüssel …), ebenfalls ins daemon.log.

    Wird von cli.main aufgerufen; bereits protokollierte Fehler (`ss_logged`) werden nicht doppelt geschrieben."""
    if getattr(error, 'ss_logged', False):
        return
    try:
        from ..scheduler import clean_error
        root = pathlib.Path(getattr(args, 'root', None) or os.getcwd()).resolve()
        handler = _setup_file_log(root)
        try:
            logging.getLogger('daemon').error('Der Hintergrunddienst ist nicht angelaufen: %s: %s',
                                              type(error).__name__, clean_error(error))
        finally:
            logging.getLogger().removeHandler(handler)
            handler.close()
    except Exception:
        pass


def run(args, cfg):
    from .. import daemon
    root = pathlib.Path(args.root or os.getcwd()).resolve()
    os.chdir(root)  # Cookies und Profile liegen relativ zum Projektordner
    handler = _setup_file_log(root)
    try:
        ran = daemon.run(root, once=args.once, poll=max(1, args.poll))
    except daemon.AlreadyRunning as error:
        logging.getLogger('daemon').info('%s', error)
        if sys.stdout: print(error)
        return 0
    except Exception as error:
        # Solange der Datei-Handler aktiv ist, die Ursache festhalten (pythonw hat keine Konsole).
        from ..scheduler import clean_error
        logging.getLogger('daemon').error('Der Hintergrunddienst ist nicht angelaufen oder abgebrochen: %s: %s',
                                          type(error).__name__, clean_error(error))
        logging.getLogger('daemon').debug('Einzelheiten', exc_info=True)
        error.ss_logged = True
        raise
    finally:
        logging.getLogger().removeHandler(handler)
        handler.close()
    if args.once and sys.stdout:
        print(f'fertig: {ran} Ziele ausgeführt')
    return 0
