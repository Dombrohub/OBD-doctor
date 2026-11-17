"""Command-line entry point (used for the obd-eft-cli.exe build)."""
import sys

for stream in (sys.stdout, sys.stderr):  # Windows consoles may not be UTF-8
    try:
        stream.reconfigure(errors="replace")
    except Exception:
        pass

from obd_doctor.__main__ import main

sys.exit(main())
