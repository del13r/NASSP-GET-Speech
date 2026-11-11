"""Entry point: ``python -m apollo_speaker``."""
from __future__ import annotations

import argparse
import sys
import os

from .config import Config, default_config_path


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m apollo_speaker", description="NASSP GET Speech")
    ap.add_argument("--config", help=f"config file (default: {default_config_path()})")
    ap.add_argument("--transcript", help="override transcript JSON path (saved to config)")
    ap.add_argument("--model-dir", help="override Piper models folder (saved to config)")
    ap.add_argument("--simulate", metavar="GET", help="simulate the GET pipe starting at e.g. +000:00:00")
    ap.add_argument("--accel", type=float, default=1.0, help="time acceleration for --simulate")
    args = ap.parse_args(argv)

    from PySide6.QtWidgets import QApplication
    from PySide6.QtGui import QIcon

    from . import theme
    from .main_window import MainWindow

    cfg_path = args.config
    config = Config.load(cfg_path)
    if args.transcript:
        config.transcript_file = args.transcript
    if args.model_dir:
        config.model_dir = args.model_dir

    app = QApplication(sys.argv[:1])
    app.setApplicationName("NASSP GET Speech")
    app.setStyle("Fusion")
    app.setStyleSheet(theme.STYLESHEET)

    # load Apollo icon from assets/icons
    base_dir = os.path.dirname(os.path.dirname(__file__))  # project root
    icon_path = os.path.join(base_dir, "assets", "icons", "apollo.ico")
    app.setWindowIcon(QIcon(icon_path))

    sim = (args.simulate, args.accel) if args.simulate else None
    win = MainWindow(config, simulate=sim)
    win.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
