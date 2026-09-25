"""PyInstaller entry point for the standalone `tele-local` binary (no Telethon inside)."""

from tele_cli.cli.local import main

if __name__ == "__main__":
    raise SystemExit(main())
