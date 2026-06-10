import os
import winreg
from .utils import PYTHON_ENV

def register_self_as_handler():
    python_exe = PYTHON_ENV.get("pythonw")
    script_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "__main__.py"))
    command = f'"{python_exe}" "{script_path}" "%1"'

    key = r"Software\Classes\nxm"
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, key) as k:
        winreg.SetValueEx(k, "", 0, winreg.REG_SZ, "URL:nxm Protocol")
        winreg.SetValueEx(k, "URL Protocol", 0, winreg.REG_SZ, "")
        with winreg.CreateKey(k, r"shell\open\command") as cmd_key:
            winreg.SetValueEx(cmd_key, "", 0, winreg.REG_SZ, command)

    print("[+] nxmhandler registered as nxm:// handler.")
