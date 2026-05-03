#!/usr/bin/env python3
"""
pyrcedit - Edit resources of Windows PE files (.exe / .dll)
A Python reimplementation of the archived rcedit tool by GitHub/Electron.

Supports Windows only (uses ctypes to call Win32 APIs).

Usage:
    pyrcedit <filename> [options...]

Options:
    -h, --help                                  Show this message
    -v. --version                               Print current version of the PyRcEdit
    --repo                                      Get the official repo link and credits
    --set-version-string <key> <value>          Set version string
    --get-version-string <key>                  Print version string
    --set-file-version <version>                Set FileVersion (e.g. 1.2.3.4)
    --set-product-version <version>             Set ProductVersion (e.g. 1.2.3.4)
    --set-icon <path-to-ico>                    Set file icon
    --set-requested-execution-level <level>     asInvoker | highestAvailable | requireAdministrator
    --application-manifest <path-to-file>       Set application manifest from file
    --set-resource-string <id> <value>          Set string resource by numeric ID
    --get-resource-string <id>                  Get string resource by numeric ID
    --set-rcdata <id> <path-to-file>            Replace RCDATA resource by numeric ID
"""

import sys
import os
import struct
import ctypes
import ctypes.wintypes
import re
import argparse
import copy
import io
from pathlib import Path
import shlex

# ── Win32 constants ──────────────────────────────────────────────────────────
LOAD_LIBRARY_AS_DATAFILE = 0x00000002
RT_VERSION   = 16
RT_GROUP_ICON = 14
RT_ICON      = 3
RT_MANIFEST  = 24
RT_STRING    = 6
RT_RCDATA    = 10

LANG_NEUTRAL  = 0
SUBLANG_NEUTRAL = 0
LANG_ENGLISH_US = 1033

VS_FF_DEBUG        = 0x00000001
VS_FF_PRERELEASE   = 0x00000002
VS_FF_PATCHED      = 0x00000004
VS_FF_PRIVATEBUILD = 0x00000008
VS_FF_INFOINFERRED = 0x00000010
VS_FF_SPECIALBUILD = 0x00000020

VOS_NT_WINDOWS32  = 0x00040004
VFT_APP           = 0x00000001
VFT2_UNKNOWN      = 0x00000000

# ── Win32 API signatures ─────────────────────────────────────────────────────
# CRITICAL: all functions that return or receive a HANDLE/pointer must use
# c_void_p, not the default c_int, to avoid 64-bit truncation on 64-bit Windows.

def _configure_win32_apis():
    vp    = ctypes.c_void_p
    ul    = ctypes.c_ulong
    wstr  = ctypes.c_wchar_p
    bo    = ctypes.c_int      # Win32 BOOL = 4-byte int, NOT ctypes.c_bool (1 byte)
    cp    = ctypes.c_char_p   # for byte-buffer lpData parameters

    k = ctypes.windll.kernel32

    # LoadLibraryExW(lpLibFileName, hFile, dwFlags) -> HMODULE
    k.LoadLibraryExW.restype  = vp
    k.LoadLibraryExW.argtypes = [wstr, vp, ul]

    # FreeLibrary(hLibModule) -> BOOL
    k.FreeLibrary.restype  = bo
    k.FreeLibrary.argtypes = [vp]

    # FindResourceW(hModule, lpName, lpType) -> HRSRC
    k.FindResourceW.restype  = vp
    k.FindResourceW.argtypes = [vp, vp, vp]

    # LoadResource(hModule, hResInfo) -> HGLOBAL
    k.LoadResource.restype  = vp
    k.LoadResource.argtypes = [vp, vp]

    # LockResource(hResData) -> LPVOID
    k.LockResource.restype  = vp
    k.LockResource.argtypes = [vp]

    # SizeofResource(hModule, hResInfo) -> DWORD
    k.SizeofResource.restype  = ul
    k.SizeofResource.argtypes = [vp, vp]

    # BeginUpdateResourceW(pFileName, bDeleteExistingResources) -> HANDLE
    k.BeginUpdateResourceW.restype  = vp
    k.BeginUpdateResourceW.argtypes = [wstr, bo]

    # UpdateResourceW(hUpdate, lpType, lpName, wLanguage, lpData, cbData) -> BOOL
    # lpData MUST be c_char_p (not c_void_p) so ctypes correctly passes buffer address
    k.UpdateResourceW.restype  = bo
    k.UpdateResourceW.argtypes = [vp, vp, vp, ctypes.c_ushort, cp, ul]

    # EndUpdateResourceW(hUpdate, fDiscard) -> BOOL
    # NOTE: the actual file write happens here — its return value MUST be checked
    k.EndUpdateResourceW.restype  = bo
    k.EndUpdateResourceW.argtypes = [vp, bo]

    # EnumResourceLanguagesW(hModule, lpType, lpName, lpEnumFunc, lParam) -> BOOL
    k.EnumResourceLanguagesW.restype  = bo
    k.EnumResourceLanguagesW.argtypes = [vp, vp, vp, vp, vp]

_configure_win32_apis()

# Callback type for EnumResourceLanguagesW
_ENUMRESLANGPROC = ctypes.WINFUNCTYPE(
    ctypes.c_int,        # return BOOL
    ctypes.c_void_p,     # hModule
    ctypes.c_void_p,     # lpType
    ctypes.c_void_p,     # lpName
    ctypes.c_ushort,     # wLanguage
    ctypes.c_void_p,     # lParam
)

def _enum_resource_language(hmod, res_type: int, res_id: int) -> int:
    """Return the language ID of the first matching resource, or LANG_ENGLISH_US."""
    found = []

    @_ENUMRESLANGPROC
    def _cb(hm, lpt, lpn, lang, lp):
        found.append(lang)
        return 0  # stop after first

    ctypes.windll.kernel32.EnumResourceLanguagesW(
        ctypes.c_void_p(hmod),
        ctypes.c_void_p(res_type),
        ctypes.c_void_p(res_id),
        _cb, None)
    return found[0] if found else LANG_ENGLISH_US

# ── Helpers ───────────────────────────────────────────────────────────────────

def _pad4(n):
    """Round up to next multiple of 4."""
    return (n + 3) & ~3

def _to_wstr(s: str) -> bytes:
    return (s + "\0").encode("utf-16-le")

def _parse_version(v: str):
    """Parse '1.2.3.4' → (1, 2, 3, 4).  Missing parts default to 0."""
    parts = v.split(".")
    parts += ["0"] * (4 - len(parts))
    try:
        return tuple(int(p) for p in parts[:4])
    except ValueError:
        raise ValueError(f"Invalid version string: {v!r}")

# ── VersionInfo serialisation / deserialisation ────────────────────────────

class VersionInfo:
    """
    Parses and serialises the VS_VERSIONINFO binary blob stored in a PE's
    RT_VERSION resource.

    Binary layout (all WORD-aligned):
        VS_VERSIONINFO header
          VS_FIXEDFILEINFO (if wValueLength > 0)
          StringFileInfo block(s)
          VarFileInfo block
    """

    def __init__(self):
        # VS_FIXEDFILEINFO fields
        self.file_version    = (1, 0, 0, 0)   # (major, minor, patch, build)
        self.product_version = (1, 0, 0, 0)
        self.file_flags_mask = 0x3F
        self.file_flags      = 0
        self.file_os         = VOS_NT_WINDOWS32
        self.file_type       = VFT_APP
        self.file_subtype    = VFT2_UNKNOWN
        self.file_date       = (0, 0)          # (high, low)

        # string tables: list of dicts {encoding:(langid, codepage), strings:{key:value}}
        self.string_tables = []

        # VarFileInfo translations: list of (langid, codepage)
        self.translations = []

    # ── Parse ──────────────────────────────────────────────────────────────

    @classmethod
    def from_bytes(cls, data: bytes) -> "VersionInfo":
        vi = cls()
        vi._parse(data)
        return vi

    def _parse(self, data: bytes):
        off = 0

        def read_header():
            nonlocal off
            w_len, w_val_len, w_type = struct.unpack_from("<HHH", data, off)
            off += 6
            # read null-terminated UTF-16LE key
            key_start = off
            while off + 1 < len(data):
                ch = struct.unpack_from("<H", data, off)[0]
                off += 2
                if ch == 0:
                    break
            key = data[key_start:off - 2].decode("utf-16-le", errors="replace")
            # align to 4 bytes
            if off % 4:
                off += 4 - (off % 4)
            return w_len, w_val_len, w_type, key

        root_start = off
        w_len, w_val_len, w_type, key = read_header()   # "VS_VERSION_INFO"

        if w_val_len == 52:  # VS_FIXEDFILEINFO present
            sig, struc_ver, fv_ms, fv_ls, pv_ms, pv_ls, \
                ffmask, ff, fos, ftype, fsubtype, fdhi, fdlo = \
                struct.unpack_from("<IIIIIIIIIIIII", data, off)
            off += 52
            if sig == 0xFEEF04BD:
                self.file_version    = ((fv_ms >> 16) & 0xffff, fv_ms & 0xffff,
                                        (fv_ls >> 16) & 0xffff, fv_ls & 0xffff)
                self.product_version = ((pv_ms >> 16) & 0xffff, pv_ms & 0xffff,
                                        (pv_ls >> 16) & 0xffff, pv_ls & 0xffff)
                self.file_flags_mask = ffmask
                self.file_flags      = ff
                self.file_os         = fos
                self.file_type       = ftype
                self.file_subtype    = fsubtype
                self.file_date       = (fdhi, fdlo)

        if off % 4:
            off += 4 - (off % 4)

        # Children: StringFileInfo and/or VarFileInfo
        while off < root_start + w_len:
            child_start = off
            c_len, c_val_len, c_type, c_key = read_header()
            if c_key == "StringFileInfo":
                self._parse_string_file_info(data, off, child_start + c_len)
            elif c_key == "VarFileInfo":
                self._parse_var_file_info(data, off, child_start + c_len)
            off = child_start + c_len
            if off % 4:
                off += 4 - (off % 4)

    def _parse_string_file_info(self, data, off, end):
        while off < end:
            tbl_start = off
            t_len, t_val_len, t_type = struct.unpack_from("<HHH", data, off)
            off += 6
            # key is 8-hex-char language+codepage
            key_start = off
            while off + 1 < len(data):
                ch = struct.unpack_from("<H", data, off)[0]
                off += 2
                if ch == 0:
                    break
            enc_key = data[key_start:off - 2].decode("utf-16-le", errors="replace")
            if off % 4:
                off += 4 - (off % 4)
            # parse langid + codepage from key
            try:
                lang_id  = int(enc_key[:4], 16)
                code_page = int(enc_key[4:], 16)
            except Exception:
                lang_id, code_page = LANG_ENGLISH_US, 1200

            strings = {}
            while off < tbl_start + t_len:
                s_start = off
                s_len, s_val_len, s_type = struct.unpack_from("<HHH", data, off)
                off += 6
                # key
                k_start = off
                while off + 1 < len(data):
                    ch = struct.unpack_from("<H", data, off)[0]
                    off += 2
                    if ch == 0:
                        break
                s_key = data[k_start:off - 2].decode("utf-16-le", errors="replace")
                if off % 4:
                    off += 4 - (off % 4)
                # value (wchar string)
                v_bytes = s_val_len * 2
                if v_bytes > 0:
                    v_raw = data[off:off + v_bytes]
                    s_val = v_raw.decode("utf-16-le", errors="replace").rstrip("\0")
                else:
                    s_val = ""
                strings[s_key] = s_val
                off = s_start + s_len
                if off % 4:
                    off += 4 - (off % 4)

            self.string_tables.append({"lang": lang_id, "cp": code_page, "strings": strings})
            off = tbl_start + t_len
            if off % 4:
                off += 4 - (off % 4)

    def _parse_var_file_info(self, data, off, end):
        # Parse Var block: series of DWORD (langid | codepage<<16)
        # skip the Var header first
        v_start = off
        v_len, v_val_len, v_type = struct.unpack_from("<HHH", data, off)
        off += 6
        k_start = off
        while off + 1 < len(data):
            ch = struct.unpack_from("<H", data, off)[0]
            off += 2
            if ch == 0:
                break
        if off % 4:
            off += 4 - (off % 4)
        n = v_val_len // 4
        for _ in range(n):
            dw = struct.unpack_from("<I", data, off)[0]
            off += 4
            self.translations.append((dw & 0xffff, (dw >> 16) & 0xffff))

    # ── Getters / setters ──────────────────────────────────────────────────

    def get_string(self, key: str) -> str | None:
        for tbl in self.string_tables:
            if key in tbl["strings"]:
                return tbl["strings"][key]
        return None

    def set_string(self, key: str, value: str):
        if not self.string_tables:
            self.string_tables.append({"lang": LANG_ENGLISH_US, "cp": 1200, "strings": {}})
        for tbl in self.string_tables:
            tbl["strings"][key] = value

    def set_file_version(self, v1, v2, v3, v4):
        self.file_version = (v1, v2, v3, v4)

    def set_product_version(self, v1, v2, v3, v4):
        self.product_version = (v1, v2, v3, v4)

    # ── Serialise ──────────────────────────────────────────────────────────

    def to_bytes(self) -> bytes:
        buf = io.BytesIO()

        def write_w(v): buf.write(struct.pack("<H", v))
        def write_dw(v): buf.write(struct.pack("<I", v))
        def write_wstr(s):
            buf.write(_to_wstr(s))
        def pad4():
            p = buf.tell() % 4
            if p: buf.write(b"\x00" * (4 - p))

        def make_block(key: str, value_bytes: bytes, children_bytes: bytes, is_text: bool) -> bytes:
            inner = io.BytesIO()
            inner.write(struct.pack("<HHH", 0, len(value_bytes) // 2 if is_text else len(value_bytes),
                                   1 if is_text else 0))
            inner.write(_to_wstr(key))
            p = inner.tell() % 4
            if p: inner.write(b"\x00" * (4 - p))
            inner.write(value_bytes)
            p = inner.tell() % 4
            if p: inner.write(b"\x00" * (4 - p))
            inner.write(children_bytes)
            total = inner.tell()
            inner.seek(0)
            inner.write(struct.pack("<H", total))  # patch wLength
            return inner.getvalue()

        # Build VS_FIXEDFILEINFO
        fv1, fv2, fv3, fv4 = self.file_version
        pv1, pv2, pv3, pv4 = self.product_version
        ffi = struct.pack("<IIIIIIIIIIIII",
            0xFEEF04BD,  # signature
            0x00010000,  # strucVersion
            (fv1 << 16) | fv2, (fv3 << 16) | fv4,  # file version
            (pv1 << 16) | pv2, (pv3 << 16) | pv4,  # product version
            self.file_flags_mask, self.file_flags,
            self.file_os, self.file_type, self.file_subtype,
            self.file_date[0], self.file_date[1])

        # Build StringFileInfo children
        sfi_children = b""
        for tbl in self.string_tables:
            lang_key = f"{tbl['lang']:04X}{tbl['cp']:04X}"
            # Build individual String entries
            str_entries = b""
            for k, v in tbl["strings"].items():
                v_bytes = _to_wstr(v)
                # each String: header(6) + key + pad + value
                inner = io.BytesIO()
                inner.write(struct.pack("<HHH", 0, len(v) + 1, 1))
                inner.write(_to_wstr(k))
                p = inner.tell() % 4
                if p: inner.write(b"\x00" * (4 - p))
                inner.write(v_bytes)
                total = inner.tell()
                inner.seek(0)
                inner.write(struct.pack("<H", total))
                # pad entry to 4 bytes
                entry = inner.getvalue()
                p = len(entry) % 4
                if p: entry += b"\x00" * (4 - p)
                str_entries += entry

            # StringTable block
            st_inner = io.BytesIO()
            st_inner.write(struct.pack("<HHH", 0, 0, 1))
            st_inner.write(_to_wstr(lang_key))
            p = st_inner.tell() % 4
            if p: st_inner.write(b"\x00" * (4 - p))
            st_inner.write(str_entries)
            total = st_inner.tell()
            st_inner.seek(0)
            st_inner.write(struct.pack("<H", total))
            sfi_children += st_inner.getvalue()

        # StringFileInfo block
        sfi_blk = io.BytesIO()
        sfi_blk.write(struct.pack("<HHH", 0, 0, 1))
        sfi_blk.write(_to_wstr("StringFileInfo"))
        p = sfi_blk.tell() % 4
        if p: sfi_blk.write(b"\x00" * (4 - p))
        sfi_blk.write(sfi_children)
        total = sfi_blk.tell()
        sfi_blk.seek(0)
        sfi_blk.write(struct.pack("<H", total))
        sfi_bytes = sfi_blk.getvalue()
        p = len(sfi_bytes) % 4
        if p: sfi_bytes += b"\x00" * (4 - p)

        # VarFileInfo block
        translations = self.translations or [(LANG_ENGLISH_US, 1200)]
        var_val = b"".join(struct.pack("<HH", lang, cp) for lang, cp in translations)
        var_inner = io.BytesIO()
        var_inner.write(struct.pack("<HHH", 0, len(var_val), 0))
        var_inner.write(_to_wstr("Translation"))
        p = var_inner.tell() % 4
        if p: var_inner.write(b"\x00" * (4 - p))
        var_inner.write(var_val)
        total = var_inner.tell()
        var_inner.seek(0)
        var_inner.write(struct.pack("<H", total))
        var_entry = var_inner.getvalue()
        p = len(var_entry) % 4
        if p: var_entry += b"\x00" * (4 - p)

        vfi_inner = io.BytesIO()
        vfi_inner.write(struct.pack("<HHH", 0, 0, 1))
        vfi_inner.write(_to_wstr("VarFileInfo"))
        p = vfi_inner.tell() % 4
        if p: vfi_inner.write(b"\x00" * (4 - p))
        vfi_inner.write(var_entry)
        total = vfi_inner.tell()
        vfi_inner.seek(0)
        vfi_inner.write(struct.pack("<H", total))
        vfi_bytes = vfi_inner.getvalue()

        # Root VS_VERSIONINFO block
        children = sfi_bytes + vfi_bytes
        root = io.BytesIO()
        root.write(struct.pack("<HHH", 0, 52, 0))
        root.write(_to_wstr("VS_VERSION_INFO"))
        p = root.tell() % 4
        if p: root.write(b"\x00" * (4 - p))
        root.write(ffi)
        p = root.tell() % 4
        if p: root.write(b"\x00" * (4 - p))
        root.write(children)
        total = root.tell()
        root.seek(0)
        root.write(struct.pack("<H", total))
        return root.getvalue()


# ── Icon parsing ───────────────────────────────────────────────────────────

class IcoFile:
    """Parse a .ico file and extract individual PNG/BMP images."""

    def __init__(self, path: str):
        with open(path, "rb") as f:
            data = f.read()
        reserved, img_type, count = struct.unpack_from("<HHH", data, 0)
        if img_type != 1:
            raise ValueError("Not a valid .ico file")
        self.images = []
        off = 6
        for _ in range(count):
            width, height, color_count, res, planes, bit_count, size, image_off = \
                struct.unpack_from("<BBBBHHII", data, off)
            off += 16
            img_data = data[image_off:image_off + size]
            self.images.append({
                "width": width or 256,
                "height": height or 256,
                "color_count": color_count,
                "reserved": res,
                "planes": planes,
                "bit_count": bit_count,
                "data": img_data,
            })


# ── Manifest helpers ────────────────────────────────────────────────────────

_MANIFEST_TEMPLATE = """\
<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<assembly xmlns="urn:schemas-microsoft-com:asm.v1" manifestVersion="1.0">
  <trustInfo xmlns="urn:schemas-microsoft-com:asm.v3">
    <security>
      <requestedPrivileges>
        <requestedExecutionLevel level="{level}" uiAccess="false"/>
      </requestedPrivileges>
    </security>
  </trustInfo>
</assembly>
"""

_VALID_LEVELS = {"asInvoker", "highestAvailable", "requireAdministrator"}

_LEVEL_RE = re.compile(
    r'(<requestedExecutionLevel[^>]*level\s*=\s*")[^"]*(")',
    re.IGNORECASE
)


# ── ResourceUpdater ────────────────────────────────────────────────────────

class ResourceUpdater:
    """High-level API mirroring rcedit's ResourceUpdater class."""

    def __init__(self):
        self._filename: str = ""
        self._version_info: VersionInfo | None = None
        self._version_lang: int = LANG_ENGLISH_US  # actual language of existing resource
        self._icon_path: str | None = None
        self._execution_level: str | None = None
        self._manifest_content: str | None = None
        self._string_changes: dict[int, str] = {}   # RT_STRING resource changes
        self._rcdata_changes: dict[int, bytes] = {}  # RT_RCDATA changes
        self._loaded = False

    # ── Load ────────────────────────────────────────────────────────────────

    def load(self, filename: str) -> bool:
        if not os.path.exists(filename):
            return False
        self._filename = filename
        # Load current version info
        self._version_info = self._read_version_info()
        if self._version_info is None:
            self._version_info = VersionInfo()  # start fresh
        self._loaded = True
        return True

    def _read_version_info(self) -> VersionInfo | None:
        kernel32 = ctypes.windll.kernel32
        hmod = kernel32.LoadLibraryExW(self._filename, None, LOAD_LIBRARY_AS_DATAFILE)
        if not hmod:
            return None
        try:
            # Discover the actual language ID so we write back to the same slot
            self._version_lang = _enum_resource_language(hmod, RT_VERSION, 1)
            hrsrc = kernel32.FindResourceW(hmod, 1, RT_VERSION)
            if not hrsrc:
                return None
            hglobal = kernel32.LoadResource(hmod, hrsrc)
            if not hglobal:
                return None
            ptr = kernel32.LockResource(hglobal)
            size = kernel32.SizeofResource(hmod, hrsrc)
            data = ctypes.string_at(ptr, size)
            return VersionInfo.from_bytes(data)
        finally:
            kernel32.FreeLibrary(hmod)

    # ── Version string ──────────────────────────────────────────────────────

    def set_version_string(self, key: str, value: str) -> bool:
        if self._version_info is None:
            return False
        self._version_info.set_string(key, value)
        return True

    def get_version_string(self, key: str) -> str | None:
        if self._version_info is None:
            return None
        return self._version_info.get_string(key)

    # ── File / product version ──────────────────────────────────────────────

    def set_file_version(self, v1, v2, v3, v4) -> bool:
        if self._version_info is None:
            return False
        self._version_info.set_file_version(v1, v2, v3, v4)
        return True

    def set_product_version(self, v1, v2, v3, v4) -> bool:
        if self._version_info is None:
            return False
        self._version_info.set_product_version(v1, v2, v3, v4)
        return True

    # ── Icon ────────────────────────────────────────────────────────────────

    def set_icon(self, path: str) -> bool:
        if not os.path.exists(path):
            return False
        self._icon_path = path
        return True

    # ── Execution level ─────────────────────────────────────────────────────

    def set_execution_level(self, level: str) -> bool:
        if level not in _VALID_LEVELS:
            return False
        self._execution_level = level
        return True

    def is_execution_level_set(self) -> bool:
        return self._execution_level is not None

    # ── Application manifest ────────────────────────────────────────────────

    def set_application_manifest(self, path: str) -> bool:
        if not os.path.exists(path):
            return False
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            self._manifest_content = f.read()
        return True

    def is_application_manifest_set(self) -> bool:
        return self._manifest_content is not None

    # ── String resource ─────────────────────────────────────────────────────

    def change_string(self, key_id: int, value: str) -> bool:
        self._string_changes[key_id] = value
        return True

    def get_string(self, key_id: int) -> str | None:
        kernel32 = ctypes.windll.kernel32
        hmod = kernel32.LoadLibraryExW(self._filename, None, LOAD_LIBRARY_AS_DATAFILE)
        if not hmod:
            return None
        try:
            buf = ctypes.create_unicode_buffer(4096)
            chars = ctypes.windll.user32.LoadStringW(hmod, key_id, buf, 4096)
            if chars == 0:
                return None
            return buf.value
        finally:
            kernel32.FreeLibrary(hmod)

    # ── RCDATA ──────────────────────────────────────────────────────────────

    def change_rcdata(self, key_id: int, path: str) -> bool:
        if not os.path.exists(path):
            return False
        with open(path, "rb") as f:
            self._rcdata_changes[key_id] = f.read()
        return True

    # ── Commit ──────────────────────────────────────────────────────────────

    def commit(self) -> bool:
        kernel32 = ctypes.windll.kernel32

        handle = kernel32.BeginUpdateResourceW(self._filename, False)
        if not handle:
            return False

        ok = True

        # ── Version info ──
        # Use _version_lang (detected from the existing resource) so we overwrite
        # the correct language slot instead of creating a duplicate under a different ID
        if self._version_info is not None:
            vi_bytes = self._version_info.to_bytes()
            res = kernel32.UpdateResourceW(
                handle, RT_VERSION, 1,
                self._version_lang,
                vi_bytes, len(vi_bytes))
            if not res:
                ok = False

        # ── Icon ──
        if ok and self._icon_path:
            ok = self._write_icon(handle)

        # ── Manifest ──
        if ok:
            manifest = self._build_manifest()
            if manifest is not None:
                manifest_bytes = manifest.encode("utf-8")
                res = kernel32.UpdateResourceW(
                    handle, RT_MANIFEST, 1,
                    LANG_ENGLISH_US,
                    manifest_bytes, len(manifest_bytes))
                if not res:
                    ok = False

        # ── String resources ──
        if ok and self._string_changes:
            ok = self._write_string_resources(handle)

        # ── RCDATA ──
        if ok and self._rcdata_changes:
            for key_id, data in self._rcdata_changes.items():
                res = kernel32.UpdateResourceW(
                    handle, RT_RCDATA, key_id,
                    LANG_ENGLISH_US,
                    data, len(data))
                if not res:
                    ok = False
                    break

        # EndUpdateResourceW does the actual file write — its return value must be checked
        end_res = kernel32.EndUpdateResourceW(handle, not ok)  # False=commit, True=discard
        if ok and not end_res:
            return False
        return ok

    def _build_manifest(self) -> str | None:
        if self._manifest_content:
            if self._execution_level:
                # patch level into existing manifest
                return _LEVEL_RE.sub(
                    lambda m: m.group(1) + self._execution_level + m.group(2),
                    self._manifest_content)
            return self._manifest_content
        if self._execution_level:
            return _MANIFEST_TEMPLATE.format(level=self._execution_level)
        return None

    def _write_icon(self, handle) -> bool:
        kernel32 = ctypes.windll.kernel32
        try:
            ico = IcoFile(self._icon_path)
        except Exception as e:
            print(f"Error reading icon: {e}", file=sys.stderr)
            return False

        # Write individual RT_ICON resources (IDs 1..n)
        for i, img in enumerate(ico.images, start=1):
            data = img["data"]
            res = kernel32.UpdateResourceW(
                handle, RT_ICON, i,
                LANG_ENGLISH_US,
                data, len(data))
            if not res:
                return False

        # Write RT_GROUP_ICON (GRPICONHEADER)
        # GRPICONENTRY layout (pragma pack 2 in C++, 14 bytes per entry):
        # B width, B height, B colourCount, B reserved,
        # B planes(=0), B bitCount, H bytesInRes (data size), H bytesInRes2(=0), H reserved2(=0), H id
        count = len(ico.images)
        grp = struct.pack("<HHH", 0, 1, count)
        for i, img in enumerate(ico.images, start=1):
            grp += struct.pack("<BBBBBBHHHH",
                img["width"] if img["width"] < 256 else 0,
                img["height"] if img["height"] < 256 else 0,
                img["color_count"],
                img["reserved"],
                0,                   # planes = 0
                img["bit_count"],    # bitCount
                len(img["data"]),    # bytesInRes = actual data size
                0,                   # bytesInRes2 = 0
                0,                   # reserved2 = 0
                i)                   # id (WORD)
        res = kernel32.UpdateResourceW(
            handle, RT_GROUP_ICON, 1,
            LANG_ENGLISH_US,
            grp, len(grp))
        return bool(res)

    def _write_string_resources(self, handle) -> bool:
        """
        String resources are stored in blocks of 16. Block N contains
        strings with IDs (N-1)*16 .. N*16-1.
        """
        kernel32 = ctypes.windll.kernel32

        # Group changes by block
        blocks: dict[int, dict[int, str]] = {}
        for sid, val in self._string_changes.items():
            block = (sid // 16) + 1
            idx   = sid % 16
            blocks.setdefault(block, {})[idx] = val

        for block_id, strings in blocks.items():
            # Read existing block if present
            existing = [""] * 16
            hmod = kernel32.LoadLibraryExW(self._filename, None, LOAD_LIBRARY_AS_DATAFILE)
            if hmod:
                hrsrc = kernel32.FindResourceW(hmod, block_id, RT_STRING)
                if hrsrc:
                    hglobal = kernel32.LoadResource(hmod, hrsrc)
                    ptr = kernel32.LockResource(hglobal)
                    size = kernel32.SizeofResource(hmod, hrsrc)
                    raw = ctypes.string_at(ptr, size)
                    off = 0
                    for i in range(16):
                        if off + 2 > len(raw):
                            break
                        length = struct.unpack_from("<H", raw, off)[0]
                        off += 2
                        if length:
                            existing[i] = raw[off:off + length * 2].decode("utf-16-le")
                            off += length * 2
                kernel32.FreeLibrary(hmod)

            # Apply changes
            for idx, val in strings.items():
                existing[idx] = val

            # Serialise block
            buf = b""
            for s in existing:
                enc = s.encode("utf-16-le")
                buf += struct.pack("<H", len(s)) + enc

            res = kernel32.UpdateResourceW(
                handle, RT_STRING, block_id,
                LANG_ENGLISH_US,
                buf, len(buf))
            if not res:
                return False
        return True


# ── CLI ────────────────────────────────────────────────────────────────────

def fatal(msg: str) -> int:
    print(f"Fatal error: {msg}", file=sys.stderr)
    return 1

def warn(msg: str):
    print(f"Warning: {msg}", file=sys.stderr)

def main(argv=None):
    if argv is None:
        argv = sys.argv[1:]

    if not sys.platform.startswith("win"):
        print("pyrcedit only works on Windows (requires Win32 API).", file=sys.stderr)
        sys.exit(1)

    if not argv or argv[0] in ("-h", "--help"):
        print(__doc__)
        return 0
    
    if not argv or argv[0] in ("-v", "--version"):
        PyRcEditVersion = "1.0.1"
        print(PyRcEditVersion)
        return 0
    
    if not argv or argv[0] in ("--repo"):
        # Don't change this string. This is the official repo link. If you going to build forked version, please just add your repo link bellow the offical one.
        PyRcEditRepo = "Official Github Repo - https://github.com/pixcapsoft/PyRcEdit\n\nFeel free to contribute & star the project\nBuilt By Ranuja Sanmira\nCopyright © 2026 PixCap Soft"
        print(PyRcEditRepo)
        return 0

    if len(argv) == 1 and os.path.isdir(argv[0]):
        target_dir = argv[0]
        prec_path = os.path.join(target_dir, "pyrcedit.prec")
        if os.path.exists(prec_path):
            try:
                with open(prec_path, "r", encoding="utf-8") as f:
                    content = f.read()
                
                # If a specific local directory was passed, we should change the working dir to it
                # so that relative paths in pyrcedit.prec are interpreted correctly.
                os.chdir(target_dir)
                
                argv = shlex.split(content, posix=False)
                if not argv:
                    return fatal(f"Configuration file is empty: {prec_path}")
            except Exception as e:
                return fatal(f"Failed to read {prec_path}: {e}")
        else:
            return fatal(f"Configuration file not found: {prec_path}")

    updater = ResourceUpdater()
    loaded = False
    i = 0

    while i < len(argv):
        arg = argv[i]

        if arg in ("--set-version-string", "-svs"):
            if i + 2 >= len(argv):
                return fatal("--set-version-string requires 'Key' and 'Value'")
            key, value = argv[i + 1], argv[i + 2]
            i += 3
            if not updater.set_version_string(key, value):
                return fatal("Unable to change version string")

        elif arg in ("--get-version-string", "-gvs"):
            if i + 1 >= len(argv):
                return fatal("--get-version-string requires 'Key'")
            key = argv[i + 1]
            i += 2
            result = updater.get_version_string(key)
            if result is None:
                return fatal("Unable to get version string")
            print(result, end="")
            return 0  # read-only

        elif arg in ("--set-file-version", "-sfv"):
            if i + 1 >= len(argv):
                return fatal("--set-file-version requires a version string")
            ver_str = argv[i + 1]
            i += 2
            try:
                v1, v2, v3, v4 = _parse_version(ver_str)
            except ValueError as e:
                return fatal(str(e))
            if not updater.set_file_version(v1, v2, v3, v4):
                return fatal("Unable to change file version")
            updater.set_version_string("FileVersion", ver_str)

        elif arg in ("--set-product-version", "-spv"):
            if i + 1 >= len(argv):
                return fatal("--set-product-version requires a version string")
            ver_str = argv[i + 1]
            i += 2
            try:
                v1, v2, v3, v4 = _parse_version(ver_str)
            except ValueError as e:
                return fatal(str(e))
            if not updater.set_product_version(v1, v2, v3, v4):
                return fatal("Unable to change product version")
            updater.set_version_string("ProductVersion", ver_str)

        elif arg in ("--set-icon", "-si"):
            if i + 1 >= len(argv):
                return fatal("--set-icon requires path to the icon")
            if not updater.set_icon(argv[i + 1]):
                return fatal("Unable to set icon (file not found?)")
            i += 2

        elif arg in ("--set-requested-execution-level", "-srel"):
            if i + 1 >= len(argv):
                return fatal("--set-requested-execution-level requires asInvoker, highestAvailable or requireAdministrator")
            level = argv[i + 1]
            i += 2
            if updater.is_application_manifest_set():
                warn("--set-requested-execution-level is ignored if --application-manifest is set")
            if not updater.set_execution_level(level):
                return fatal(f"Invalid execution level '{level}'. Use: asInvoker, highestAvailable, requireAdministrator")

        elif arg in ("--application-manifest", "-am"):
            if i + 1 >= len(argv):
                return fatal("--application-manifest requires local path")
            if updater.is_execution_level_set():
                warn("--set-requested-execution-level is ignored if --application-manifest is set")
            if not updater.set_application_manifest(argv[i + 1]):
                return fatal("Unable to read manifest file")
            i += 2

        elif arg in ("--set-resource-string", "--srs"):
            if i + 2 >= len(argv):
                return fatal("--set-resource-string requires int 'Key' and string 'Value'")
            try:
                key_id = int(argv[i + 1])
            except ValueError:
                return fatal("Unable to parse id")
            if not updater.change_string(key_id, argv[i + 2]):
                return fatal("Unable to change string")
            i += 3

        elif arg in ("--get-resource-string", "-grs"):
            if i + 1 >= len(argv):
                return fatal("--get-resource-string requires int 'Key'")
            try:
                key_id = int(argv[i + 1])
            except ValueError:
                return fatal("Unable to parse id")
            result = updater.get_string(key_id)
            if result is None:
                return fatal("Unable to get resource string")
            print(result, end="")
            return 0

        elif arg == "--set-rcdata":
            if i + 2 >= len(argv):
                return fatal("--set-rcdata requires int 'Key' and path to resource 'Value'")
            try:
                key_id = int(argv[i + 1])
            except ValueError:
                return fatal("Unable to parse id")
            if not updater.change_rcdata(key_id, argv[i + 2]):
                return fatal("Unable to change RCDATA (file not found?)")
            i += 3

        else:
            # Must be the exe/dll filename — first positional argument
            if loaded:
                print(f'Unrecognized argument: "{arg}"', file=sys.stderr)
                return 1
            loaded = True
            if not updater.load(arg):
                print(f'Unable to load file: "{arg}"', file=sys.stderr)
                return 1
            i += 1

    if not loaded:
        return fatal("You should specify a exe/dll file")

    if not updater.commit():
        return fatal("Unable to commit changes")

    return 0


if __name__ == "__main__":
    sys.exit(main())
