# Advanced: ARM64 / OCI-style hosted Jellii setup

**New to Jellii? Start with [Local-first Quick Start](local-first.md)** instead. This page covers the more demanding always-on / publicly reachable deployment after your first local FAST result.

**Public Alpha: executable reference, not yet independently live-E2E verified.** Start here if you want to build and use your own StickS3. This recipe preserves the original TLS-PSK device transport and separately authenticated HTTPS Grok callback. It assumes a *single trusted Ubuntu ARM64 Linux host* with Docker Engine 28+, Docker Compose v2, Python 3.13, Git and internet access. A StickS3, its USB flashing connection, and Gemini API credentials are required. The Grok Bot Routine is needed for SLOW tasks.

### Network plan

You need two real DNS names resolving directly to your host (for example, a device hostname and a callback hostname), and inbound TCP **443** (raw device TLS-PSK), **9443** (Grok HTTPS callback via Caddy), and **80** (ACME HTTP-01 validation). Port 443 must not be used by a normal HTTPS reverse proxy. For a home-hosted system, forward these ports at your router. **If your Grok Bot's HTTPS tool cannot call port 9443, this single-IP plan is unsuitable.** Use a separately validated standard-443 HTTPS callback endpoint instead; do not weaken authentication. This is a reference topology, not a universal production installer.

## 1. Get source and apply the pinned firmware patch

Run from your ARM64 Linux host; use a separate trusted machine for firmware flashing when your server is remote.

```sh
git clone https://github.com/smallgun01/Grok-Amusant.git ~/Grok-Amusant
cd ~/Grok-Amusant
git clone https://github.com/zbruceli/kai.git ../kai-build
git -C ../kai-build checkout --detach 1681fb3c11acd28525d8987bba4b9bf4bc2a288f
git -C ../kai-build apply --check "$PWD/patches/amusant-sticks3-base-v0.12.0.patch"
git -C ../kai-build apply "$PWD/patches/amusant-sticks3-base-v0.12.0.patch"
```

**PASS:** Patch applies cleanly to the pinned Kai revision. For patch details see [firmware instructions](../patches/amusant-sticks3-base-v0.12.0.md). Firmware and server secret material must never enter Git.

## 2. Make matching, private credentials

Replace the hostnames in the following command with domains you actually control. Do not use the placeholders as real configuration.

```sh
python3 deploy/selfhost/prepare.py \
  --device-host device.your-real-domain.net \
  --callback-host callback.your-real-domain.net \
  --private-dir "$HOME/amusant-private" \
  --firmware-dir "$HOME/kai-build/firmware"
```

The script prompts **without echo** for the Gemini key and Grok Routine incoming bearer token; pressing Enter at the second prompt generates a random token you must also configure in Grok. Optionally pass `--routine-url https://YOUR_REAL_ROUTINE/WEBHOOK`, or edit the private `AMUSANT_GROK_ROUTINE_URL` later. Without a real Routine URL, **FAST-first is possible but SLOW is disabled**. This script creates:

- `~/amusant-private/private.env` — Compose variable values, including local image tags, callback URL, and secret-file paths.
- `~/amusant-private/secrets/{gemini_api_key,device_token,device_psks,grok_routine_token}` — never commit or share.
- `~/amusant-private/Caddyfile` — private HTTPS callback proxy configuration.
- `~/kai-build/firmware/src/secrets.h` — matching device PSK identity/key, token and Relay hostname.

The script rejects overwrites and does not print credentials. It does not modify active services.

**On standard rootful Linux Docker**, match the Relay's secret-file and SQLite owner checks:

```sh
sudo chown root:20000 "$HOME"/amusant-private/secrets/*
sudo chmod 0440 "$HOME"/amusant-private/secrets/*
sudo chown -R 10001:10001 "$HOME"/amusant-private/data/relay
sudo chmod 0700 "$HOME"/amusant-private/data/relay
```

Do not `chown` the private environment file or firmware `secrets.h`. File-backed Compose secrets are not automatically remapped for container UID/GID; check the *in-container* ownership and permissions if startup fails. Do not disable the fail-closed permission validation. Rootless Docker and NAS setups need independent checking.

## 3. Build, configure and start the Relay + Egress proxy

```sh
cd ~/Grok-Amusant
docker build --platform linux/arm64 -f deploy/oci-a1/Dockerfile.relay -t amusant-relay:selfhost .
docker build --platform linux/arm64 -f deploy/oci-a1/Dockerfile.egress-proxy -t amusant-egress:selfhost .
docker compose --env-file "$HOME/amusant-private/private.env" \
  -f deploy/oci-a1/compose.grok.yaml -f deploy/selfhost/compose.ports.yaml config --quiet
docker compose --env-file "$HOME/amusant-private/private.env" \
  -f deploy/oci-a1/compose.grok.yaml -f deploy/selfhost/compose.ports.yaml up -d
docker compose --env-file "$HOME/amusant-private/private.env" \
  -f deploy/oci-a1/compose.grok.yaml -f deploy/selfhost/compose.ports.yaml ps
```

**PASS:** Both containers are running/healthy and SQLite persists across a container restart. The small [ports override](../deploy/selfhost/compose.ports.yaml) preserves raw TCP TLS-PSK handshake on host port 443, forwards it to the Relay's private 8443, and leaves the Callback Listener on its own private Docker bridge interface `RELAY_CALLBACK_BIND_IP:8645` (default `172.30.74.2:8645`), **not a host-published port**. Host Caddy must explicitly reach that private bridge address on a trusted rootful Linux Docker host; verify reachability before internet use. Inspect local Compose logs if startup fails; check secret permissions, private subnet conflicts and occupied ports. Never publish the internal proxy or raw callback port directly.

## 4. Configure HTTPS callback, separate from StickS3 TLS-PSK

Install [Caddy on the host](https://caddyserver.com/docs/install). The setup script generated the hostname-specific Caddyfile proxying to the separate private callback Docker bridge IP (default `172.30.74.2:8645`) using HTTPS callback port 9443 and ACME **HTTP-01** on 80 (the TLS-ALPN challenge is disabled so 443 remains for PSK):

```sh
sudo install -o root -g root -m 0644 "$HOME/amusant-private/Caddyfile" /etc/caddy/Caddyfile
sudo caddy validate --config /etc/caddy/Caddyfile
sudo systemctl enable --now caddy
sudo systemctl reload caddy
```

**PASS:** With a real DNS name and issued certificate, externally probe the HTTPS callback with an *unauthenticated* request:

```sh
curl -i -X POST 'https://callback.your-real-domain.net:9443/callback/results' \
  -H 'Content-Type: application/json' -d '{}'
```

**HTTP 401** is expected from the Relay: TLS, Caddy forwarding and the callback receiver are reachable, but the request intentionally lacks a task capability. Confirm the host can reach the callback bridge address `172.30.74.2:8645` and that Caddy uses that exact upstream; do not expose it publicly. HTTP 404/502, TLS failure or timeout are **not** PASS. Caddy must preserve the request Host (including :9443) and path. The device WSS endpoint is different from this callback URL.

## 5. Validate, build and flash the real StickS3

Use the generated private `secrets.h`, *not* the synthetic firmware CI values. The firmware must point to the same device hostname and PSK/token as the Relay.

```sh
cd ~/Grok-Amusant
python3 patches/sticks3-baseline/preflash.py \
  "$HOME/kai-build/firmware/src/secrets.h" \
  --expected-host device.your-real-domain.net
python3 -m venv .venv-firmware
. .venv-firmware/bin/activate
python -m pip install platformio==6.2.0 -r patches/kai-v0.12.0-wifi-w1/tools/requirements.txt
platformio run -d ../kai-build/firmware -e sticks3
platformio run -d ../kai-build/firmware -e sticks3 -t upload
```

**PASS:** Preflash PASS, successful PlatformIO build, verified USB flash. For a remotely hosted Relay, firmware building/flashing can be done on your trusted local computer using the same pinned source and securely transferred private configuration. Never commit a firmware binary containing your credentials.

## 6. Provision the device's Wi-Fi

Follow the [BLE/PoP/QR and USB recovery guide](wifi-provisioning.md). On the first USB-powered boot with **no saved Wi-Fi**, Jellii displays a **26-character PoP (not QR)**. Use a compatible Espressif BLE Provisioning app, find `PROV_XXXXXX`, enter the current PoP and send 2.4 GHz Wi-Fi credentials. For changing Wi-Fi later: hold side **B for 5 seconds** while Idle/Offline; the device restarts into the QR owner-setup screen, front **A** toggles QR/PoP and B cancels.

**PASS:** StickS3 joins Wi-Fi with an IP and subsequently connects to your Relay via **TLS-PSK/WSS on 443**. Wi-Fi alone is not proof of WSS connectivity.

## 7. First FAST voice test, then SLOW

**FAST:** With Relay healthy and device connected, hold front A, ask “What is a webhook?”, release, and hear a spoken answer from **Gemini Live through Relay**. If SLOW has no valid Grok Routine, clearly label the milestone FAST-only.

**SLOW:** Set up *your own* Grok Bot Routine: it must accept an HTTPS POST authenticated by the `grok_routine_token` bearer token with JSON `schema_version`, `task_id`, `result_token`, `task_label`, `instruction`, and respond HTTP 200 when admitted. After executing, it must POST a JSON result to `https://callback.YOUR_DOMAIN:9443/callback/results` using `Authorization: Bearer <received result_token>`. **Never put the per-task capability in JSON or logs.** The body must follow [the exact result schema](../relay/grok_result.py) (`schema_version` = `amusant-grok-pilot-0`, `task_id`, `reported_outcome`, `speak`, `card` with `title` and 1–4 `lines`). Set the private `AMUSANT_GROK_ROUTINE_URL` to the Routine webhook and restart Relay. A callback proves receipt of an Agent report, not independent proof of Grok's native run.

**SLOW PASS:** Ask Jellii for a delegated research task; verify Webhook accepted, subsequent callback HTTP 200, result in SQLite/Inbox, then side B reads the unread result aloud using **Gemini Live** and the unread badge clears.

## Acceptance and limits

| Gate | PASS means |
| --- | --- |
| Code | Pinned patch, preflash and firmware build pass |
| Server | Relay/Egress healthy; SQLite durable; secrets accessible only as authorized |
| Network | Device TLS-PSK on 443, separate authenticated HTTPS callback on 9443 |
| Wi-Fi | Physical BLE/PoP or QR provisioning succeeds |
| FAST | Live spoken Gemini answer on StickS3 |
| SLOW | Real Grok Routine → authenticated callback → Inbox → B spoken result |

**Not yet verified:** This reference deployment has not been independently completed from a clean machine through physical E2E. The original owner's Amusant runtime acceptance is separate. Local CI, syntax checks and firmware builds cannot replace it. Grok Bot Routine UI/HTTP tool permissions vary and must be established independently; if your provider cannot POST to :9443, adopt a separately verified secure ingress topology instead of altering core authentication.

Further detail: [Architecture](architecture.md) · [Configuration](configuration.md) · [Offline tests](quickstart.md).
