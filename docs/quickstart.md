# Offline tests and builds

## Python

Use Python 3.13 and uv. From the repository root:

```sh
uv sync --locked --extra hardware-opus --group dev
uv run --locked python -m unittest relay.tests.test_grok_callback relay.tests.test_inbox fast_voice.tests.test_contract
```

The three-module command is a quick check. `.github/workflows/offline.yml` lists the complete offline selection, including device and callback security, preflash, and Wi-Fi provisioning. Tests use fakes and local loopback resources rather than real Gemini or Grok Bot calls.

## Images

The manual `build-images` workflow builds Relay and Egress Proxy with a clean Docker cache. Select `amd64` or `arm64`; ARM64 uses QEMU and is a separate run. The workflow loads images only into the runner and does not publish them. Image builds do not validate production ingress or paid provider integration.

## Firmware

The manual `firmware-build` workflow checks out pinned Kai, applies the consolidated patch, runs selected host regressions and compiles StickS3 with synthetic configuration. See [firmware instructions](../patches/amusant-sticks3-base-v0.12.0.md) for local commands and the preflash boundary. It does not flash hardware.
