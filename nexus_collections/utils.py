import ctypes
import logging
import os
import shutil
import subprocess
import sys
import zipfile
from ctypes import byref, create_unicode_buffer, pointer, WINFUNCTYPE
from ctypes.wintypes import DWORD, WCHAR, UINT

LIBS_FOLDER = os.path.abspath(os.path.join(os.path.dirname(__file__), "libs"))

if LIBS_FOLDER not in sys.path:
    sys.path.insert(0, LIBS_FOLDER)

import requests
import py7zr

ERROR_SUCCESS = 0
ERROR_MORE_DATA = 234
RmForceShutdown = 1

rstrtmgr = ctypes.windll.LoadLibrary("Rstrtmgr")

_7z = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "data", "7z.exe"))
DATA_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "nexus_collections")
os.makedirs(DATA_PATH, exist_ok=True)

log_path = os.path.join(DATA_PATH, "nexus_collections.log")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(message)s",
    handlers=[
        logging.FileHandler(log_path, encoding="utf-8"),
        logging.StreamHandler(),
    ]
)
logger = logging.getLogger()

if "PyQt6" in sys.modules:
    from PyQt6.QtWidgets import QMessageBox, QWidget, QDialog, QVBoxLayout, QLineEdit, QLabel, QPushButton

    def show_popup(parent: QWidget, title: str, message: str):
        msg_box = QMessageBox(parent)
        msg_box.setIcon(QMessageBox.Icon.Information)
        msg_box.setWindowTitle(title)
        msg_box.setText(message)
        msg_box.setStandardButtons(QMessageBox.StandardButton.Ok)
        msg_box.exec()


    def ask_yes_no(title="Confirm", message="Are you sure?"):
        reply = QMessageBox.question(None, title, message,
                                     QMessageBox.Yes | QMessageBox.No,
                                     QMessageBox.No)

        return reply == QMessageBox.Yes


@WINFUNCTYPE(None, UINT)
def _callback(percent_complete: UINT) -> None:
    print(f"Unlocking file status: {percent_complete}% done")

def unlock_file(file_path: str) -> None:
    """
    Use Windows Restart Manager API to unlock a file locked by other processes.
    """
    session_handle = DWORD(0)
    session_flags = DWORD(0)
    session_key = (WCHAR * 256)()

    result = DWORD(rstrtmgr.RmStartSession(byref(session_handle), session_flags, session_key)).value
    if result != ERROR_SUCCESS:
        raise RuntimeError(f"RmStartSession returned non-zero result: {result}")

    try:
        file_buffer = pointer(create_unicode_buffer(file_path))
        result = DWORD(rstrtmgr.RmRegisterResources(session_handle, 1, byref(file_buffer), 0, None, 0, None)).value
        if result != ERROR_SUCCESS:
            raise RuntimeError(f"RmRegisterResources returned non-zero result: {result}")

        proc_info_needed = DWORD(0)
        proc_info = DWORD(0)
        reboot_reasons = DWORD(0)

        result = DWORD(rstrtmgr.RmGetList(session_handle, byref(proc_info_needed), byref(proc_info), None, byref(reboot_reasons))).value
        if result not in (ERROR_SUCCESS, ERROR_MORE_DATA):
            raise RuntimeError(f"RmGetList returned non-successful result: {result}")

        if proc_info_needed.value:
            result = DWORD(rstrtmgr.RmShutdown(session_handle, RmForceShutdown, _callback)).value
            if result != ERROR_SUCCESS:
                raise RuntimeError(f"RmShutdown returned non-successful result: {result}")
        else:
            print("File is not locked")
    finally:
        result = DWORD(rstrtmgr.RmEndSession(session_handle)).value
        if result != ERROR_SUCCESS:
            raise RuntimeError(f"RmEndSession returned non-successful result: {result}")

def download_file(url: str, save: str, chunk: int = 8192):
    with requests.get(url, stream=True) as r:
        r.raise_for_status()
        with open(save, 'wb') as f:
            for chunk in r.iter_content(chunk_size=chunk):
                f.write(chunk)

def extract_all(archive_path: str, output_folder: str):
    if zipfile.is_zipfile(archive_path):
        with zipfile.ZipFile(archive_path, 'r') as archive:
            archive.extractall(output_folder)
    else:
        if py7zr.is_7zfile(archive_path):
            try:
                with py7zr.SevenZipFile(archive_path, 'r') as e:
                    e.extractall(output_folder)
                    return
            except:
                pass

            if not os.path.exists(_7z):
                os.makedirs(os.path.dirname(_7z), exist_ok=True)
                download_file("https://www.7-zip.org/a/7zr.exe", _7z)

            subprocess.run([_7z, "x", os.path.abspath(archive_path), f"-o{os.path.abspath(output_folder)}", "-y"], check=True)

            contents = os.listdir(output_folder)
            if len(contents) == 1:
                top = os.path.join(output_folder, contents[0])
                if os.path.isdir(top):
                    for item in os.listdir(top):
                        shutil.move(os.path.join(top, item), output_folder)
                    os.rmdir(top)

def write_all(input_folder: str, archive_path: str):
    input_folder = os.path.abspath(input_folder)
    archive_path = os.path.abspath(archive_path)

    if archive_path.lower().endswith('.zip'):
        with zipfile.ZipFile(archive_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
            for root, dirs, files in os.walk(input_folder):
                for file in files:
                    full_path = os.path.join(root, file)
                    rel_path = os.path.relpath(full_path, input_folder)
                    zipf.write(full_path, rel_path)
    else:
        try:
            with py7zr.SevenZipFile(archive_path, 'w') as e:
                e.writeall(input_folder)
        except:
            # why the fuck does 7zip need admin
            if not os.path.exists(_7z):
                os.makedirs(os.path.dirname(_7z), exist_ok=True)
                download_file("https://www.7-zip.org/a/7zr.exe", _7z)

            subprocess.run([_7z, "a", os.path.abspath(archive_path), os.path.abspath(os.path.join(input_folder, '*'))], check=True)
