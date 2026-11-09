#include "inbox_play.h"
#include <cassert>

int main() {
  using inbox_play::State;
  State s;
  assert(!s.eligible(true, true, false));  // no synchronization
  s.sync(1);
  assert(s.eligible(true, true, false));
  assert(!s.eligible(false, true, false));
  assert(!s.eligible(true, false, false));  // LISTENING, THINKING, REPLY
  assert(!s.eligible(true, true, true));  // provisioning
  s.pending = true;
  assert(!s.eligible(true, true, false));
  s.pending = false;
  s.press_eligible = s.eligible(true, false, false); // pressed while busy
  assert(!(s.press_eligible && s.eligible(true, true, false))); // idle before release
  for (int wake = 0; wake < 2; ++wake) {  // deep sleep and screen-off
    s.suppress_wake = true;
    assert(s.suppress(true));  // wake press/hold
    assert(s.suppress(false)); // release (including wasClicked) is consumed
    assert(!s.suppress(false));
    assert(s.eligible(true, true, false)); // a subsequent press may play
  }
  s.disconnect();
  assert(!s.eligible(true, true, false));
  s.sync(0);
  assert(!s.eligible(true, true, false));
  s.sync(1);
  assert(s.eligible(true, true, false));
}
