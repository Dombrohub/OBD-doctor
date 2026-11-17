# PyInstaller build recipe for OBD Doctor.
#   pyinstaller obd_doctor.spec   ->  dist/OBDDoctor/OBDDoctor.exe        (GUI)
#                                     dist/OBDDoctor/obd-doctor-cli.exe   (command line)
#                                     dist/OBDDoctor/_internal/           (shared Python runtime + libraries)
#
# Both programs live in one folder and share a single copy of Python and the
# libraries, so the download is about half the size of two self-contained .exe
# files, and the apps start faster (nothing has to be unpacked on each launch).
import sys

ICON = "obd_doctor/assets/icon.ico" if sys.platform == "win32" else None
VERSION = "version_info.txt" if sys.platform == "win32" else None
DATAS = [("obd_doctor/assets/icon.ico", "obd_doctor/assets"),
         ("obd_doctor/assets/icon.png", "obd_doctor/assets")]
HIDDEN = ["obd_doctor.__main__", "obd_doctor.simulator", "obd_doctor.gui",
          "obd_doctor.ai", "obd_doctor.config", "obd_doctor.modules", "obd_doctor.history", "obd_doctor.support", "obd_doctor.vin", "obd_doctor.adaptercheck", "obd_doctor.update", "anthropic",
          "serial", "serial.tools.list_ports"]
EXCLUDES = ["numpy", "PIL", "matplotlib", "pytest"]

gui_a = Analysis(["obd_doctor_gui.pyw"], pathex=["."], datas=DATAS, hiddenimports=HIDDEN, excludes=EXCLUDES)
cli_a = Analysis(["obd_doctor_cli.py"], pathex=["."], datas=DATAS, hiddenimports=HIDDEN, excludes=EXCLUDES)

gui_exe = EXE(PYZ(gui_a.pure), gui_a.scripts, [], exclude_binaries=True, name="OBDDoctor",
              console=False, icon=ICON, version=VERSION, upx=False)
cli_exe = EXE(PYZ(cli_a.pure), cli_a.scripts, [], exclude_binaries=True, name="obd-doctor-cli",
              console=True, icon=ICON, version=VERSION, upx=False)

COLLECT(gui_exe, cli_exe,
        gui_a.binaries, gui_a.datas,
        cli_a.binaries, cli_a.datas,
        name="OBDDoctor", upx=False)
