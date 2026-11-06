"""Run GlowShift: ``python -m glowshift``."""
import logging
import tkinter as tk

from glowshift import diagnostics
from glowshift.gui.app import App

logger = logging.getLogger('glowshift')


def main():
    handler = diagnostics.configure()
    root = tk.Tk()
    app = App(root, on_connection_event=diagnostics.record_connection_event)
    try:
        root.mainloop()
    finally:
        if not app.shutdown():
            logger.warning('Bluetooth did not stop within the shutdown limit')
        logging.getLogger().removeHandler(handler)
        handler.close()


if __name__ == '__main__':
    main()
