# Consolidated StickS3 patch

Apply `amusant-sticks3-base-v0.12.0.patch` to Kai commit `1681fb3c11acd28525d8987bba4b9bf4bc2a288f` with `git apply --check` before `git apply`. This candidate has not completed a clean firmware build. Install the pinned build dependencies and run host regressions before any hardware use.

Run `python patches/sticks3-baseline/preflash.py /path/to/secrets.h --expected-host YOUR_ACTUAL_HOST` locally. The provided host must match the header and must not be a reserved example or placeholder domain. Never commit the header or its values.