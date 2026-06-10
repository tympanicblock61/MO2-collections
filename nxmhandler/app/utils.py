import configparser
import os
import shutil
import socket
import subprocess
import sys
import sysconfig
from urllib.parse import urlparse, parse_qs

def get_server_port():
    config = configparser.ConfigParser()
    config.optionxform = str
    config.read(INI_PATH)
    return int(config.get("current_config", "port"))

def is_server_running(host='localhost', timeout=1.0):
    try:
        with socket.create_connection((host, get_server_port()), timeout=timeout):
            return True
    except (ConnectionRefusedError, socket.timeout, OSError):
        return False


def is_valid_python_exe(path: str) -> bool:
    try:
        subprocess.check_output([path, '--version'], stderr=subprocess.STDOUT, text=True)
        return True
    except Exception:
        return False


def build_python_env(exe_path: str) -> dict:
    base_dir = os.path.dirname(os.path.abspath(exe_path))
    root = os.path.dirname(base_dir) if os.path.basename(base_dir).lower() == "scripts" else base_dir

    env = {
        "python": os.path.abspath(os.path.join(root, "python.exe")),
        "pythonw": os.path.abspath(os.path.join(root, "pythonw.exe")),
        "scripts": os.path.abspath(os.path.join(root, "Scripts")),
        "dlls": os.path.abspath(os.path.join(root, "DLLs")),
        "site_packages": os.path.abspath(sysconfig.get_paths(vars={"base": root, "platbase": root})["purelib"]),
        "root": os.path.abspath(root),
    }

    return env


def find_python_environment() -> dict:
    def check_and_build(path: str) -> dict | None:
        if path and is_valid_python_exe(path):
            return build_python_env(path)
        return None

    env = check_and_build(sys.executable)
    if env:
        return env

    for ver in [f"-3.{v}" for v in range(1, 13)]:
        path = shutil.which(f"py{ver}")
        env = check_and_build(path)
        if env:
            return env

    for ver in [f"3{v}" for v in range(1, 13)]:
        for path in [
            rf"C:\Python{ver}\python.exe",
            rf"C:\Program Files\Python{ver}\python.exe",
            rf"C:\Program Files (x86)\Python{ver}\python.exe",
        ]:
            env = check_and_build(path)
            if env:
                return env

    env = check_and_build(shutil.which("python"))
    if env:
        return env

    raise RuntimeError("No valid Python environment found.")

class NxmUrl:
    def __init__(self, url):
        self.raw_url = url
        self.parsed = urlparse(url)
        self.game = self.parsed.netloc.lower()
        self.path = self.parsed.path.strip("/").split("/")
        self.query = parse_qs(self.parsed.query)

    def is_valid(self):
        return self.parsed.scheme == "nxm" and len(self.path) >= 2

class CollectionUrl(NxmUrl):
    def __init__(self, url):
        super().__init__(url)
        self.slug = self.path[1]
        self.revision = self.path[3] if len(self.path) > 3 and self.path[2] == "revisions" else None

    def __repr__(self):
        return f"CollectionUrl(game={self.game}, slug={self.slug}, revision={self.revision})"

class ModUrl(NxmUrl):
    def __init__(self, url):
        super().__init__(url)
        self.mod_id = self.path[1]
        self.file_id = self.path[3] if len(self.path) > 3 and self.path[2] == "files" else None

    def __repr__(self):
        return f"ModUrl(game={self.game}, mod_id={self.mod_id}, file_id={self.file_id}, query={self.query})"

class UnknownNxmUrl(NxmUrl):
    def __repr__(self):
        return f"UnknownNxmUrl(game={self.game}, path={self.path}, query={self.query})"

def parse_nxm_url(url):
    parsed = urlparse(url)
    path = parsed.path.strip("/").split("/")
    if parsed.scheme != "nxm" or len(path) < 2:
        return UnknownNxmUrl(url)

    type_hint = path[0].lower()
    if type_hint == "collections":
        return CollectionUrl(url)
    elif type_hint == "mods":
        return ModUrl(url)
    else:
        return UnknownNxmUrl(url)

def add_local_install():
    for need in PYTHON_ENV.keys():
        if os.path.isdir(PYTHON_ENV.get(need)):
            if PYTHON_ENV.get(need) not in sys.path:
                sys.path.append(PYTHON_ENV.get(need))
                os.add_dll_directory(PYTHON_ENV.get(need))

PYTHON_ENV = find_python_environment()
INI_PATH = os.path.join(os.path.dirname(__file__), "nxmhandler.ini")