"""Keep provider keys in macOS Keychain, never in research records or argv."""

import ctypes as C
import sys


class Keychain:
    service = b"org.nexusresearch.connections"

    def __init__(self):
        self.available = sys.platform == "darwin"

    def _libraries(self):
        if not self.available:
            raise ValueError("Secure API key storage requires macOS Keychain in this release.")
        sec = C.CDLL("/System/Library/Frameworks/Security.framework/Security")
        cf = C.CDLL("/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation")
        sec.SecKeychainFindGenericPassword.argtypes = [
            C.c_void_p,
            C.c_uint32,
            C.c_char_p,
            C.c_uint32,
            C.c_char_p,
            C.POINTER(C.c_uint32),
            C.POINTER(C.c_void_p),
            C.POINTER(C.c_void_p),
        ]
        sec.SecKeychainAddGenericPassword.argtypes = [
            C.c_void_p,
            C.c_uint32,
            C.c_char_p,
            C.c_uint32,
            C.c_char_p,
            C.c_uint32,
            C.c_void_p,
            C.POINTER(C.c_void_p),
        ]
        sec.SecKeychainItemModifyAttributesAndData.argtypes = [
            C.c_void_p,
            C.c_void_p,
            C.c_uint32,
            C.c_void_p,
        ]
        sec.SecKeychainItemDelete.argtypes = [C.c_void_p]
        sec.SecKeychainItemFreeContent.argtypes = [C.c_void_p, C.c_void_p]
        cf.CFRelease.argtypes = [C.c_void_p]
        return sec, cf

    def _find(self, account):
        sec, cf = self._libraries()
        account = account.encode()
        length, data, item = C.c_uint32(), C.c_void_p(), C.c_void_p()
        status = sec.SecKeychainFindGenericPassword(
            None,
            len(self.service),
            self.service,
            len(account),
            account,
            C.byref(length),
            C.byref(data),
            C.byref(item),
        )
        if status == -25300:
            return sec, cf, None, None
        if status:
            raise ValueError(
                "Keychain access was declined or unavailable. Unlock it and try again."
            )
        value = C.string_at(data, length.value).decode()
        sec.SecKeychainItemFreeContent(None, data)
        return sec, cf, item, value

    def get(self, account):
        sec, cf, item, value = self._find(account)
        if item:
            cf.CFRelease(item)
        if not value:
            raise ValueError("Add this connection's API key in Connections first.")
        return value

    def set(self, account, value):
        if not isinstance(value, str) or not 8 <= len(value) <= 4096 or "\n" in value:
            raise ValueError("Enter a valid API key.")
        sec, cf, item, _ = self._find(account)
        key = value.encode()
        if item:
            status = sec.SecKeychainItemModifyAttributesAndData(item, None, len(key), key)
        else:
            name = account.encode()
            item = C.c_void_p()
            status = sec.SecKeychainAddGenericPassword(
                None,
                len(self.service),
                self.service,
                len(name),
                name,
                len(key),
                key,
                C.byref(item),
            )
        if item:
            cf.CFRelease(item)
        if status:
            raise ValueError("Could not save the API key in Keychain.")

    def delete(self, account):
        sec, cf, item, _ = self._find(account)
        if item:
            try:
                if sec.SecKeychainItemDelete(item):
                    raise ValueError("Could not remove the key from Keychain.")
            finally:
                cf.CFRelease(item)
