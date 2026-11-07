# Consolidated StickS3 firmware patch

The patch targets [Kai v0.12.0](https://github.com/zbruceli/kai) at commit `1681fb3c11acd28525d8987bba4b9bf4bc2a288f`. The `firmware-build` workflow checks that revision, applies the patch, runs selected host regressions, and compiles the `sticks3` PlatformIO environment. The build uses synthetic compile-only settings and does not flash hardware.

From the repository root on a Linux host with Git, Python 3.13, C/C++ compilers and PlatformIO Core 6.2.0, use a separate throwaway directory for Kai:

```sh
git clone https://github.com/zbruceli/kai.git ../kai-build
git -C ../kai-build checkout --detach 1681fb3c11acd28525d8987bba4b9bf4bc2a288f
git -C ../kai-build apply --check "$PWD/patches/amusant-sticks3-base-v0.12.0.patch"
git -C ../kai-build apply "$PWD/patches/amusant-sticks3-base-v0.12.0.patch"
python -m venv .venv-firmware
. .venv-firmware/bin/activate
python -m pip install platformio==6.2.0 -r patches/kai-v0.12.0-wifi-w1/tools/requirements.txt
python patches/kai-v0.12.0-wifi-w1/tests/run_host_tests.py --firmware-root ../kai-build/firmware
python patches/kai-v0.12.0-wifi-w1.5/tests/run_host_tests.py --firmware-root ../kai-build/firmware
python patches/kai-v0.12.0-wifi-w2b1/tests/run_host_tests.py --firmware-root ../kai-build/firmware --wifi-tests patches/kai-v0.12.0-wifi-w1.5/tests
python patches/kai-v0.12.0-wifi-w2bqr/tests/check_owner_setup_wiring.py --firmware-root ../kai-build/firmware
```

For a compile-only build, create `../kai-build/firmware/src/secrets.h` with the synthetic values used in `.github/workflows/firmware-build.yml`, then run `platformio run -d ../kai-build/firmware -e sticks3`. The placeholder host is deliberately invalid and the key is all zeroes; the resulting binary is not configured for use and must not be flashed. The firmware platform and library dependencies are selected by the pinned Kai `platformio.ini` plus this patch.

Before any separately authorized device operation, provide your own private configuration and run `python patches/sticks3-baseline/preflash.py /path/to/secrets.h --expected-host YOUR_ACTUAL_HOST`. The host must match, and placeholder/reserved domains and missing or invalid PSK values are rejected. Keep real configuration and credentials outside the repository. For a first LAN-only Jellii test, use [Local-first Quick Start](../docs/local-first.md): its explicit `--allow-private-ip` preflash flag accepts only RFC1918 LAN IPv4 and does not relax PSK or token requirements. Public/cloud deployments should not use the flag. See [third-party notices](../NOTICE.md) before distributing a firmware binary.
