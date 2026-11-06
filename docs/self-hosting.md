# Self-hosting Amusant / Jellii (Public Alpha)

**Start with the [step-by-step ARM64 reference installation](build-your-own-jellii.md)** for a concrete command sequence. This page explains components and safety boundaries; the reference installation is not yet independently live-E2E accepted.

This guide explains how to reproduce the source builds and what a **self-hosted working device** additionally needs. **It is not a one-command installer or a validated internet-facing deployment recipe.** The published Compose configuration is intentionally incomplete at the ingress boundaries. Do not expose it on the internet without separately designing and testing those boundaries.

## System and prerequisites

Amusant is a push-to-talk companion, not a standalone offline assistant:

| Component | What you must provide |
| --- | --- |
| Device | M5Stack StickS3 (ESP32-S3), USB connection for firmware flashing and first Wi-Fi setup |
| Firmware | Pinned Kai v0.12.0 source plus the Amusant patch; PlatformIO Core 6.2.0 |
| Relay | Python 3.13 and uv for local tests; Docker and Docker Compose for the sample runtime |
| Voice | Your own Google Gemini Live API credentials |
| Delegated tasks | A Grok Bot Routine with a working HTTPS Webhook and the ability to POST a final callback |
| Networking | Two independently secured routes: device TLS-PSK/WSS, and Grok HTTPS callback |
| Persistence | A private, durable directory for SQLite and mounted secret files |

The provided Compose file is tailored to **Linux/ARM64 (OCI A1)**. Running the sample on AMD64 requires selecting a matching image and updating the hardcoded `platform` settings; a successful AMD64 image build alone does not make the ARM64 Compose file portable. No public image registry or ingress configuration is supplied.

## 1. Reproduce the tests

From the repository root, with Python 3.13 and [uv](https://docs.astral.sh/uv/):

```sh
uv sync --locked --extra hardware-opus --group dev
uv run --locked python -m unittest relay.tests.test_grok_callback relay.tests.test_inbox fast_voice.tests.test_contract
```

The wider offline selection is recorded in [offline.yml](../.github/workflows/offline.yml). All these tests use fake providers and do not establish live service connectivity.

## 2. Build the firmware

Follow [the pinned Kai patch and PlatformIO instructions](../patches/amusant-sticks3-base-v0.12.0.md). The documented CI firmware build uses deliberately invalid synthetic credentials and **must not be flashed**.

For your own device, prepare `firmware/src/secrets.h` **inside your private Kai checkout**, not in this repository. It must supply a real device FQDN, TLS port 443, unique PSK identity, 64-hex-character nonzero PSK, and device token. The Relay must use matching credentials. Before flashing, run:

```sh
python patches/sticks3-baseline/preflash.py /path/to/private/kai/firmware/src/secrets.h --expected-host your-device-host.example.net
```

Replace the example hostname with your actual, privately configured hostname. This validation checks configuration shape and host consistency, not reachability. Follow the upstream Kai/PlatformIO flashing procedure with the validated build; this repository has not validated a public flashing workflow end to end.

**Wi-Fi credentials are separate:** after flashing, send the SSID and Wi-Fi password using [BLE or USB provisioning](wifi-provisioning.md). BLE cannot change the embedded Relay host, device PSK, device token or Gemini/Grok settings.

## 3. Configure an isolated Relay deployment

Read [configuration and safety](configuration.md), [the example environment](../deploy/oci-a1/grok-staging.env.example) and [Compose](../deploy/oci-a1/compose.grok.yaml).

The sample requires you to replace all invalid example image digests, hostnames, URLs, owner identifiers, network settings and limits, and to supply a persistent data directory. Build and pin your own Relay/Egress images; [build-images.yml](../.github/workflows/build-images.yml) shows reproducible CI image builds but **does not publish images**.

Keep secrets outside Git. The sample mounts four secret files:

| Secret | Purpose |
| --- | --- |
| `gemini_api_key` | Gemini Live authentication |
| `device_token` | Device-to-Relay application authentication |
| `device_psks` | Comma-separated `identity:64hexkey` records for TLS-PSK |
| `grok_routine_token` | Relay-to-Grok Routine Webhook bearer token |

These are **different credentials**, even when they belong to one owner. Never use the compile-only all-zero PSK or placeholder token.

The Relay entrypoint enforces strict mounted-secret file metadata: either **UID 10001 / mode 0400**, or **root-owned / GID 20000 / mode 0440** (with the container's supplemental group 20000). Check ownership and mode as seen **inside the container**, not just on the host; Docker secret/bind-mount behavior varies by environment. Relay refuses a mismatched secret mount. Protect the SQLite volume from other users and backups from accidental publication.

You may validate the shape of the Compose file after choosing real, private configuration values:

```sh
docker compose --env-file /path/to/private.env -f deploy/oci-a1/compose.grok.yaml config
```

A successful `config` command is **not** a network, secret-permission or live service acceptance test.

## 4. Provide the missing ingress boundaries

The sample intentionally declares private Docker networks and does not publish device or callback ingress ports.

- **Device:** StickS3 connects via **TLS 1.2 ECDHE-PSK + WSS** to its configured FQDN on port **443**. The private Relay device listener is on port **8443**. Any ingress must preserve the device's PSK TLS handshake end to end; an ordinary HTTPS reverse proxy that terminates certificate-based TLS is **not a drop-in replacement**.
- **Grok callback:** the separate public HTTPS endpoint must accept `POST /callback/results` and forward it to the Relay's private callback listener (sample port **8645**) without losing the expected canonical `Host`. The receiver validates a **per-task bearer capability**, trusted Host, method, JSON schema and body limits. The callback URL is not the device WSS endpoint.
- **Outbound:** the Relay sends the Grok Routine request via the separate Egress Proxy. Protect the internal proxy; do not open it as a public proxy.

These network/ingress components are **not implemented as a turnkey deployment** in this source candidate. Design and verify them in your own environment before permitting external access; do not casually publish 8443 or 8645.

## 5. Wire the Grok Bot Routine

`AMUSANT_SLOW_EXECUTOR` must be `grok_callback`; this release does not support the legacy Hermes/ACP/DynamoDB backends. Your independently configured Routine endpoint must:

1. Accept Relay's **HTTPS POST** containing JSON with `schema_version`, `task_id`, `result_token`, `task_label`, and `instruction`, authenticated by the configured Routine bearer token; respond **HTTP 200** on acceptance.
2. Execute the delegated instruction, then send **one final HTTPS POST** to the configured `/callback/results` URL.
3. Put the supplied one-task `result_token` in `Authorization: Bearer` for that callback, **not in the JSON body**. The result JSON must comply with [the bounded callback contract](../relay/grok_result.py) and [its tests](../relay/tests/test_grok_callback.py).

Receipt of a Webhook request is not task completion. The Relay records submission state in SQLite, handles callback idempotency, and makes completed results available to the owner-scoped Inbox. Unknown submissions are not blindly replayed.

The repo includes the Relay side of this contract but **does not provision a Grok Bot account or deploy your Routine for you**. Your provider must support the required HTTP calls and permissions.

## 6. Smoke-test in your own controlled environment

Verify each boundary separately before treating the system as working:

1. Offline tests and pinned firmware build pass with synthetic inputs.
2. Container config resolves; Relay/Egress start with your own secret mounts and durable SQLite directory; startup/shutdown and restart work.
3. Invalid device token, unknown PSK identity and invalid callback capability are rejected. Confirm real device TLS-PSK/WSS connectivity only on your secured device route.
4. A short PTT request reaches Gemini Live and returns spoken audio through **Gemini Live**.
5. A delegated task reaches your Grok Routine, which calls the separate authenticated callback; the result appears in SQLite/Inbox.
6. The StickS3 B-button Inbox readout is spoken via Gemini Live and clears its unread indication after retrieval, as supported by the firmware.

Do not claim steps 2–6 pass because CI tests or image compilation passed. Physical device, network ingress, real Gemini and Grok provider behavior require independent acceptance.

## Known Public Alpha limits

- No turnkey self-host installer, published production images, or universal cloud/network recipe.
- Example Compose is ARM64-specific and has intentionally missing ingress routes.
- Full live setup depends on a separately configured Grok Bot Routine and accessible HTTPS callback.
- Firmware builds and offline CI are not evidence of a newly reproduced physical E2E installation.
- Local Wi-Fi credentials are stored in device Preferences/NVS; CRC/integrity checks are **not flash encryption**. Consider physical-access security separately.

For the device's practical Wi-Fi setup and recovery steps, see [Wi-Fi provisioning](wifi-provisioning.md).
