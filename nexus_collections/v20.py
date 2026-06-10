import binascii
import ctypes
import json
import os
import pathlib

import struct
import sys
import traceback
import typing as t
from hashlib import sha256

try:
    from utils import unlock_file
except:
    from .utils import unlock_file

import pysqlite3 as sqlite3

try:
    from nxmhandler.utils import add_local_install
    add_local_install()
except ImportError:
    pass

if sys.stdout is not None:
    if not hasattr(sys.stdout, 'isatty'):
        sys.stdout.isatty = lambda: False

    if not hasattr(sys.stdout, 'encoding'):
        sys.stdout.encoding = 'utf-8'

import windows
import windows.crypto
import windows.security
from Crypto.Cipher import AES, ChaCha20_Poly1305
from windows.generated_def import SecurityImpersonation, TokenImpersonation
from windows.winobject import process
from windows.winproxy.apis import kernel32


def _is_admin():
    try:
        return ctypes.windll.shell32.IsUserAnAdmin() == 1
    except:
        return False

def run_as_admin(with_params=True):
    if sys.version_info[0] == 3 and not _is_admin():
        script = os.path.abspath(sys.argv[0])
        params = ' '.join([f'"{arg}"' for arg in sys.argv[1:]])

        if with_params:
            args = f'"{script}" {params}'
        else:
            args = ""
        ctypes.windll.shell32.ShellExecuteW(
            None, "runas", sys.executable, args, None, 1)

class Impersonator:
    def __init__(self):
        self.duplicated_token = None
        self.original_token = None

    @classmethod
    def _enable_debug_privilege(cls):
        windows.current_process.token.enable_privilege("SeDebugPrivilege")

    @classmethod
    def _find_lsass(cls) -> process.WinProcess:
        return next(p for p in windows.system.processes if p.name == "lsass.exe")

    @classmethod
    def _duplicate_system_token(cls, proc: process.WinProcess):
        return proc.token.duplicate(
            type=TokenImpersonation,
            impersonation_level=SecurityImpersonation
        )

    def start(self):
        self._enable_debug_privilege()
        proc = self._find_lsass()
        self.duplicated_token = self._duplicate_system_token(proc)
        self.original_token = windows.current_thread.token
        windows.current_thread.token = self.duplicated_token

    def close(self):
        if self.duplicated_token:
            kernel32.CloseHandle(self.duplicated_token.handle)
            self.duplicated_token = None
        windows.current_thread.token = self.original_token


LOCAL_APP_DATA = os.environ["LOCALAPPDATA"]
BROWSERS = {
    "chrome": {
        "key_path": rf"{LOCAL_APP_DATA}\Google\Chrome\User Data\Local State",
        "db_path": rf"{LOCAL_APP_DATA}\Google\Chrome\User Data\Default\Network\Cookies",
    },
    "edge": {
        "key_path": rf"{LOCAL_APP_DATA}\Microsoft\Edge\User Data\Local State",
        "db_path": rf"{LOCAL_APP_DATA}\Microsoft\Edge\User Data\Default\Network\Cookies",
    },
    "brave": {
        "key_path": rf"{LOCAL_APP_DATA}\BraveSoftware\Brave-Browser\User Data\Local State",
        "db_path": rf"{LOCAL_APP_DATA}\BraveSoftware\Brave-Browser\User Data\Default\Network\Cookies",
    }
}


def pop_from_string_front(s: bytes) -> t.Tuple[bytes, bytes]:
    size = struct.unpack("=I", s[:4])[0]
    return s[4:4 + size], s[4 + size:]

def remove_validation_data(data: bytes) -> bytes:
    _, padded = pop_from_string_front(data)
    return pop_from_string_front(padded)[0]

def decrypt_chrome_key(encrypted_key: bytes) -> bytes:
    if len(encrypted_key) == 32:
        return encrypted_key  # Not Chrome browser
    # decrypt key with AES256GCM or ChaCha20Poly1305
    # aes and chacha20 keys from elevation_service.exe
    aes_key = binascii.a2b_base64("sxxuJBrIRnKNqcH6xJNmUc/7lE0UOrgWJ2vMbaAoR4c=")
    chacha20_key = bytes.fromhex("E98F37D7F4E1FA433D19304DC2258042090E2D1D7EEA7670D41F738D08729660")
    # [flag|iv|ciphertext|tag] encrypted_key
    # [1byte|12bytes|32bytes|16bytes]
    flag = encrypted_key[0]
    iv = encrypted_key[1:1 + 12]
    ciphertext = encrypted_key[1 + 12:1 + 12 + 32]
    tag = encrypted_key[1 + 12 + 32:]
    print(f"Flag: {flag}")
    if flag == 1:
        cipher = AES.new(aes_key, AES.MODE_GCM, nonce=iv)
    elif flag == 2:
        cipher = ChaCha20_Poly1305.new(key=chacha20_key, nonce=iv)
    else:
        raise ValueError(f"Unsupported flag: {flag}")
    key = cipher.decrypt_and_verify(ciphertext, tag)
    return key

def remove_cookie_domain_hash(domain: str, cookie: bytes) -> bytes:
    if len(cookie) < 32:
        return cookie
    if sha256(domain.encode()).digest() == cookie[:32]:
        return cookie[32:]
    return cookie

# decrypt v20 cookie with AES256GCM
# [prefix|iv|ciphertext|tag] encrypted_value
# [3bytes|12bytes|variable|16bytes]
def decrypt_cookie_v20(key, encrypted_value: bytes, domain: str) -> str | bytes:
    cookie_iv = encrypted_value[3:3 + 12]
    encrypted_cookie = encrypted_value[3 + 12:-16]
    cookie_tag = encrypted_value[-16:]
    cookie_cipher = AES.new(key, AES.MODE_GCM, nonce=cookie_iv)
    decrypted_cookie = cookie_cipher.decrypt_and_verify(encrypted_cookie, cookie_tag)
    final_cookie = remove_cookie_domain_hash(domain, decrypted_cookie)
    try:
        return final_cookie.decode("utf-8")
    except UnicodeDecodeError:
        return final_cookie


def dump_cookies(output_dir="cookies", cookie_filters=None):
    os.makedirs(output_dir, exist_ok=True)

    def cookie_matches(name, domain):
        if not cookie_filters or len(cookie_filters) == 0:
            return True
        for f in cookie_filters:
            if f.get("name") == name and f.get("domain") == domain:
                return True
        return False

    for BROWSER, browser in BROWSERS.items():
        print(f"\n--- Dumping cookies from: {BROWSER} ---")
        try:
            key_path = browser["key_path"]
            db_path = browser["db_path"]

            with open(key_path, "r") as f:
                local_state = json.load(f)

            app_bound_encrypted_key = binascii.a2b_base64(local_state["os_crypt"]["app_bound_encrypted_key"])
            assert app_bound_encrypted_key.startswith(b"APPB")
            app_bound_encrypted_key = app_bound_encrypted_key[4:]

            impersonator = Impersonator()
            impersonator.start()
            encrypted_key = windows.crypto.unprotect(app_bound_encrypted_key)
            impersonator.close()

            decrypted_key = windows.crypto.unprotect(encrypted_key)
            decrypted_key = remove_validation_data(decrypted_key)
            key = decrypt_chrome_key(decrypted_key)

            unlock_file(db_path)

            con = sqlite3.connect(pathlib.Path(db_path).as_uri() + "?mode=ro", uri=True)
            cur = con.cursor()
            r = cur.execute("SELECT host_key, name, CAST(encrypted_value AS BLOB) from cookies;")
            cookies = r.fetchall()
            cookies_v20 = [c for c in cookies if c[2][:3] == b"v20"]

            con.close()

            cookie_dict = []
            for domain, name, encrypted_value in cookies_v20:
                if cookie_matches(name, domain):
                    cookie_dict.append({
                        "domain": domain,
                        "name": name,
                        "value": decrypt_cookie_v20(key, encrypted_value, domain)
                    })

            output_path = os.path.join(output_dir, f"{BROWSER.lower()}_cookies.json")
            with open(output_path, "w", encoding="utf-8") as f:
                json.dump(cookie_dict, f, indent=2)

            print(f"Saved {len(cookie_dict)} cookies to {output_path}")

        except Exception:
            traceback.print_exc()

if __name__ == "__main__":
    if not _is_admin():
        run_as_admin(True)
    else:
        filters = []
        output_dir = "../data/nexus_collections/cookies"

        if len(sys.argv) >= 2:
            output_dir = sys.argv[1]

        if len(sys.argv) >= 3:
            for arg in sys.argv[2:]:
                try:
                    domain, name = arg.split(":", 1)
                    filters.append({"domain": domain, "name": name})
                except ValueError:
                    print(f"[!] Invalid filter format: {arg} (expected domain:name)")

        dump_cookies(output_dir, filters)