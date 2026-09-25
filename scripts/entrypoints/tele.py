"""PyInstaller entry point for the standalone `tele` binary."""

from tele_cli.cli.tele import main

if __name__ == "__main__":
    raise SystemExit(main())
