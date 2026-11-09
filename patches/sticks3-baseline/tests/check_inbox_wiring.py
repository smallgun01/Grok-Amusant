"""Check the tested button policy is wired into the reconstructed firmware."""
import argparse
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument("--firmware-root", type=Path, required=True)
args = parser.parse_args()
source = (args.firmware_root / "src" / "main.cpp").read_text(encoding="utf-8")
buttons = source.split("static void handleButtons() {", 1)[1].split("\n}\n", 1)[0]
wake = buttons.split("if (screenOff && M5.BtnB.wasPressed()) {", 1)[1].split("\n  }", 1)[0]
assert wake.index("inboxPlay.suppress_wake = true") < wake.index("wake();") < wake.index("return;")
assert buttons.index("inboxPlay.suppress(M5.BtnB.isPressed())") < buttons.index("M5.BtnB.wasClicked()")
assert "!suppressB && M5.BtnB.wasClicked()" in buttons
assert "inboxPlay.press_eligible &&" in buttons
assert buttons.index('sendType("ptt_start")') < buttons.index('sendType("inbox_play")')
assert "inboxPlay.press_eligible = false;" in buttons
assert "wifi_ble_provisioning::active()" in buttons
assert "inboxPlay.suppress_wake = wakePins & (1ULL << PIN_BTN_B)" in source
assert 'case WStype_DISCONNECTED:\n      inboxPlay.disconnect();\n      face::setInbox(0);' in source
assert 'now - modeSince > THINKING_TIMEOUT_MS) {\n        inboxPlay.pending = false;' in source
print("Inbox button, wake, disconnect, timeout and provisioning wiring passed.")
