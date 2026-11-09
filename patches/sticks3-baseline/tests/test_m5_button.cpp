// Compile against the pinned M5Unified 0.2.23 Button_Class implementation.
#define M5UNIFIED_IMPLEMENTATION
#include "utility/Button_Class.inl"
#include "inbox_play.h"
#include <cassert>

int main() {
  for (int wake = 0; wake < 2; ++wake) {
    m5::Button_Class button;
    inbox_play::State state;
    state.sync(1);
    button.setRawState(20, true);
    assert(button.wasPressed());
    state.suppress_wake = true;
    button.setRawState(40, false);
    assert(button.wasReleased() && button.wasClicked());
    assert(state.suppress(button.isPressed()));
    button.setRawState(60, false);
    assert(!state.suppress(button.isPressed()));
    button.setRawState(80, true);
    assert(button.wasPressed());
    state.press_eligible = state.eligible(true, true, false);
    button.setRawState(100, false);
    assert(button.wasClicked() && !state.suppress(button.isPressed()));
    assert(state.press_eligible && state.eligible(true, true, false));
  }
}
