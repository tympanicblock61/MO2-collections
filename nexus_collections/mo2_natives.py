import ctypes
import ctypes.wintypes as wt

_advapi = ctypes.windll.LoadLibrary("advapi32")

class _FILETIME(ctypes.Structure):
    _fields_ = [("dwLowDateTime", wt.DWORD), ("dwHighDateTime", wt.DWORD)]

class CREDENTIAL(ctypes.Structure):
    _fields_ = [
        ("Flags",              wt.DWORD),
        ("Type",               wt.DWORD),
        ("TargetName",         wt.LPWSTR),
        ("Comment",            wt.LPWSTR),
        ("LastWritten",        _FILETIME),
        ("CredentialBlobSize", wt.DWORD),
        ("CredentialBlob",     ctypes.POINTER(wt.BYTE)),
        ("Persist",            wt.DWORD),
        ("AttributeCount",     wt.DWORD),
        ("Attributes",         ctypes.c_void_p),
        ("TargetAlias",        wt.LPWSTR),
        ("UserName",           wt.LPWSTR),
    ]

CRED_TYPE_GENERIC = 1


_advapi.CredReadW.restype  = wt.BOOL
_advapi.CredReadW.argtypes = [
    wt.LPCWSTR,
    wt.DWORD,
    wt.DWORD,
    ctypes.POINTER(ctypes.POINTER(CREDENTIAL)),
]

_advapi.CredFree.restype  = None
_advapi.CredFree.argtypes = [ctypes.c_void_p]

_advapi.CredEnumerateW.restype  = wt.BOOL
_advapi.CredEnumerateW.argtypes = [
    wt.LPCWSTR,
    wt.DWORD,
    ctypes.POINTER(wt.DWORD),
    ctypes.POINTER(ctypes.POINTER(ctypes.POINTER(CREDENTIAL))),
]

def _blob_to_str(cred: CREDENTIAL) -> str:
    size = cred.CredentialBlobSize
    if not size:
        return ""
    raw = bytes((wt.BYTE * size).from_address(ctypes.addressof(cred.CredentialBlob.contents)))
    return raw.decode("utf-16-le").rstrip("\x00")

def read_credential(target: str) -> dict | None:
    ptr = ctypes.POINTER(CREDENTIAL)()
    if not _advapi.CredReadW(target, CRED_TYPE_GENERIC, 0, ctypes.byref(ptr)):
        return None
    try:
        c = ptr.contents
        return {
            "target":   c.TargetName  or "",
            "username": c.UserName    or "",
            "type":     c.Type,
            "persist":  c.Persist,
            "value":    _blob_to_str(c),
        }
    finally:
        _advapi.CredFree(ptr)

def read_mo2_api_key() -> str:
    cred = read_credential("ModOrganizer2_APIKEY")
    return cred["value"] if cred else ""