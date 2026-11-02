# Amusant local candidate

An experimental push-to-talk device relay using Gemini Live, Grok callback and SQLite. This prototype is not release ready. A Grok profile and user-managed credentials are required. No real endpoint or credential is supplied. Do not deploy until privacy, licensing and build checks pass.

Run offline tests with the pinned dependencies: `uv sync --locked --extra hardware-opus --group dev` and `uv run --locked python -m unittest relay.tests.test_grok_callback relay.tests.test_inbox fast_voice.tests.test_contract`.

Firmware preflash requires `python patches/sticks3-baseline/preflash.py /path/to/secrets.h --expected-host USER_HOST`; it rejects mismatches, reserved example domains and missing PSK values. Do not flash based on this prototype alone. Clean Docker and firmware builds remain unverified. Project license is pending owner decision.