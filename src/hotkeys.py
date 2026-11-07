"""Validated, user-recorded keyboard bindings. No text input is recorded."""
import sys

CUSTOM_ACTIONS = {"play_pause": 0x5A12, "previous": 0x5A11, "next": 0x5A10, "stop": 0x5A13}
MODIFIER_KEYS = {0x10, 0x11, 0x12, 0x5B, 0x5C, 0xA0, 0xA1, 0xA2, 0xA3, 0xA4, 0xA5}


def binding(value):
    if not isinstance(value, dict):
        raise ValueError("Нажмите клавишу или сочетание.")
    key, modifiers = value.get("vk"), value.get("modifiers", 0)
    if type(key) is not int or not 8 <= key <= 254 or key in MODIFIER_KEYS or key == 27:
        raise ValueError("Выберите клавишу вместе с модификаторами. Esc отменяет запись.")
    if type(modifiers) is not int or not 0 <= modifiers <= 15:
        raise ValueError("Некорректное сочетание клавиш.")
    return {"vk": key, "modifiers": modifiers}


def normalize(value):
    result = {action: None for action in CUSTOM_ACTIONS}
    seen = set()
    if isinstance(value, dict):
        for action in result:
            try:
                item = binding(value.get(action))
            except ValueError:
                continue
            identity = (item["vk"], item["modifiers"])
            if identity not in seen:
                seen.add(identity)
                result[action] = item
    return result


def label(value):
    if value is None:
        return "Медиаклавиша"
    key = value["vk"]
    names = {0xB0: "Следующий трек", 0xB1: "Предыдущий трек", 0xB2: "Media Stop", 0xB3: "Media Play/Pause"}
    name = names.get(key, "")
    if not name and 0x70 <= key <= 0x87:
        name = "F" + str(key - 0x6F)
    if not name and sys.platform == "win32":
        import ctypes as c
        user = c.WinDLL("user32")
        scan = user.MapVirtualKeyW(key, 4)
        extended = (scan & 0xFF00) != 0
        buffer = c.create_unicode_buffer(64)
        user.GetKeyNameTextW((scan & 255) << 16 | (1 << 24 if extended else 0), buffer, 64)
        name = buffer.value
    if not name:
        name = chr(key) if 0x30 <= key <= 0x5A else "Клавиша " + hex(key)
    prefixes = [text for flag, text in [(2, "Ctrl"), (1, "Alt"), (4, "Shift"), (8, "Win")] if value["modifiers"] & flag]
    return " + ".join([*prefixes, name])
