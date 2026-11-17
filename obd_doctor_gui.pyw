"""Double-click this file (Windows/macOS) to open OBD EFT. Keep it next to the obd_doctor folder."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from obd_doctor.gui import run

run()
