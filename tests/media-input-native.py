"""Manual Windows smoke: real input and driver messages, no real music control."""
import ctypes as c
from ctypes import wintypes as w
from pathlib import Path
import sys
import threading
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.windows_media_input import WindowsMediaInput


class Keyboard(c.Structure):
    _fields_ = [("key", w.WORD), ("scan", w.WORD), ("flags", w.DWORD),
                ("time", w.DWORD), ("extra", c.c_size_t)]


class Mouse(c.Structure):
    _fields_ = [("x", w.LONG), ("y", w.LONG), ("data", w.DWORD),
                ("flags", w.DWORD), ("time", w.DWORD), ("extra", c.c_size_t)]


class Payload(c.Union):
    _fields_ = [("keyboard", Keyboard), ("mouse", Mouse)]


class Input(c.Structure):
    _fields_ = [("kind", w.DWORD), ("payload", Payload)]


def main():
    api = WindowsMediaInput()
    try:
        api.set_active(True)
        assert api.hook and api.window and api.shell_registered, "Native input setup failed"
        api.user.SendInput.argtypes = [w.UINT, c.POINTER(Input), c.c_int]
        api.user.SendInput.restype = w.UINT
        inputs = (Input * 3)()
        for index, flags in enumerate([0, 0, 2]):
            inputs[index].kind = 1
            inputs[index].payload.keyboard = Keyboard(0xB0, 0, flags, 0, 0)
        sent = []
        sender = threading.Thread(target=lambda: sent.append(api.user.SendInput(3, inputs, c.sizeof(Input))))
        sender.start()
        received = []
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline and (not received or sender.is_alive()):
            received.extend(api.messages())
            time.sleep(.01)
        sender.join(1)
        assert sent == [3] and received == [0x5A10], (sent, received)
        # Native recording and its saved F24 binding, without exposing the
        # key to another application or connecting a real audio controller.
        def press_f24():
            sequence = (Input * 2)()
            for index, flags in enumerate([0, 2]):
                sequence[index].kind = 1
                sequence[index].payload.keyboard = Keyboard(0x87, 0, flags, 0, 0)
            assert api.user.SendInput(2, sequence, c.sizeof(Input)) == 2
        api.configure({}, "capture-f24")
        sender = threading.Thread(target=press_f24)
        sender.start()
        captured, unwanted = [], []
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline and (not captured or sender.is_alive() or api.input.held):
            unwanted.extend(api.messages())
            captured.extend(api.captures())
            time.sleep(.01)
        sender.join(1)
        assert captured == [("capture-f24", {"vk": 0x87, "modifiers": 0})] and not unwanted
        api.configure({"next": {"vk": 0x87, "modifiers": 0}}, None)
        sender = threading.Thread(target=press_f24)
        sender.start()
        received = []
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline and (not received or sender.is_alive()):
            received.extend(api.messages())
            time.sleep(.01)
        sender.join(1)
        assert received == [0x5A10], received
        # No audio controller is connected: keyboard events are consumed above.
        api.user.PostMessageW.argtypes = [w.HWND, w.UINT, w.WPARAM, w.LPARAM]
        api.user.PostMessageW.restype = w.BOOL
        for message, wp, command, expected in [(0x319, 0, 12, 0x5A11),
                                               (api.shell_message, 12, 11, 0x5A10)]:
            time.sleep(.2)
            assert api.user.PostMessageW(api.window, message, wp, (command | 0x8000) << 16)
            got = []
            deadline = time.monotonic() + 1
            while not got and time.monotonic() < deadline:
                got.extend(api.messages())
                time.sleep(.01)
            assert got == [expected], got
        print("PASS: SendInput media key; native F24 capture without playback; saved F24 -> next; WM_APPCOMMAND previous; shell next; no audio commands executed")
    finally:
        api.close()
        assert not api.hook and not api.window and not api.atom
        print("Released native keyboard hook, shell registration, hidden window and window class")


if __name__ == "__main__":
    main()
