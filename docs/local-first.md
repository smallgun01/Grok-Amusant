# Local-first Jellii: first spoken FAST answer

**Start here.** This guide reproduces the original Amusant development approach: get a **real StickS3 speaking with Gemini Live over your LAN**, then add Grok SLOW and deploy to a server later.

The Amusant owner previously validated **local Ubuntu Relay ↔ physical StickS3 ↔ Gemini Live** and later validated **full FAST/SLOW with Grok callback and Inbox on OCI**. That is real project acceptance evidence, **not** proof that this newly written public installation recipe has passed an independent clean-room run. Keep those two claims separate.

## What you need

- One **Linux** desktop / mini-PC / Ubuntu server with Docker Engine + Compose v2, Git, Python 3.13, enough disk for images and PlatformIO. Native `amd64` and `arm64` are supported by the reference build; Windows/macOS can use a Linux host or VM but are not part of this recipe.
- One StickS3, a USB data cable, a phone with an Espressif-compatible BLE Wi-Fi provisioning app, and a **2.4 GHz Wi-Fi LAN**.
- A Gemini API key with Gemini Live access. **No Grok Bot, public domain, public IP, Caddy, inbound internet ports, or Cloudflare account is required for the first FAST test.**

Your Linux host and StickS3 must be reachable on the same LAN (no guest Wi-Fi/client isolation). Select a **stable RFC1918 IPv4** on the Linux host, such as `192.168.68.57`; reserve it on your router. It must actually belong to that host. This recipe uses **TLS 1.2 ECDHE-PSK over WSS on TCP 443 even on LAN**; it never downgrades to plaintext.

## 1. Clone & patch Kai

Run on Linux, using separate sibling directories:

```sh
git clone https://github.com/smallgun01/Grok-Amusant.git ~/Grok-Amusant
cd ~/Grok-Amusant
git clone https://github.com/zbruceli/kai.git ../kai-build
git -C ../kai-build checkout --detach 1681fb3c11acd28525d8987bba4b9bf4bc2a288f
git -C ../kai-build apply --check "$PWD/patches/amusant-sticks3-base-v0.12.0.patch"
git -C ../kai-build apply "$PWD/patches/amusant-sticks3-base-v0.12.0.patch"
```

**PASS:** the pinned Kai patch applies cleanly. Do not copy any real configuration into either Git repository.

## 2. Generate matching local credentials

Find your Linux host's LAN address (`ip -4 addr` on Linux). Replace the address below with **your own** host address. It must be in 10/8, 172.16/12 or 192.168/16 (not localhost).

```sh
cd ~/Grok-Amusant
python3 deploy/selfhost/prepare.py --local \
  --device-host 192.168.68.57 \
  --private-dir "$HOME/amusant-private" \
  --firmware-dir "$HOME/kai-build/firmware"
```

The tool securely prompts for your Gemini key (not shown on screen) and **creates**, outside the repo, matching device PSK, device token, the private Docker environment, a matching Kai `firmware/src/secrets.h` and an empty SQLite data directory. It refuses overwriting pre-existing files or folders. It deliberately creates an **unusable Grok Routine/callback URL** for this FAST-only stage; no SLOW task is submitted.

**Rootful Linux Docker** expects the existing Relay Secret mounts and data directory to have strict UID/GID and permissions:

```sh
sudo chown root:20000 "$HOME"/amusant-private/secrets/*
sudo chmod 0440 "$HOME"/amusant-private/secrets/*
sudo chown -R 10001:10001 "$HOME"/amusant-private/data/relay
sudo chmod 0700 "$HOME"/amusant-private/data/relay
```

Only do this with the newly generated files. Do not change the ownership of your private `secrets.h` or `private.env`. For rootless Docker, check the mapped IDs separately.

## 3. Build and launch the local containers

From the Amusant repo:

```sh
cd ~/Grok-Amusant
docker build -f deploy/oci-a1/Dockerfile.relay -t amusant-relay:selfhost .
docker build -f deploy/oci-a1/Dockerfile.egress-proxy -t amusant-egress:selfhost .
docker build -f deploy/selfhost/Dockerfile.device-ingress -t amusant-device-ingress:selfhost .
docker compose --env-file "$HOME/amusant-private/private.env" \
  -f deploy/oci-a1/compose.grok.yaml -f deploy/selfhost/compose.local.yaml config --quiet
docker compose --env-file "$HOME/amusant-private/private.env" \
  -f deploy/oci-a1/compose.grok.yaml -f deploy/selfhost/compose.local.yaml up -d
docker compose --env-file "$HOME/amusant-private/private.env" \
  -f deploy/oci-a1/compose.grok.yaml -f deploy/selfhost/compose.local.yaml ps
```

The local Compose override adds a raw TCP ingress container that **binds port 443 to the chosen host LAN IP** and forwards encrypted device traffic to Relay. Relay remains on internal networks; the ingress container cannot reach the callback network. The callback listener is *not published* and should remain disabled while Grok is unconfigured. Your host firewall must allow **TCP 443 from your LAN**; do not forward it through your home router to the internet.

**PASS:** Relay and Egress Proxy healthy, and device ingress running. Inspect logs with `docker compose --env-file "$HOME/amusant-private/private.env" -f deploy/oci-a1/compose.grok.yaml -f deploy/selfhost/compose.local.yaml logs --tail=80 relay` if not healthy. Check secret permissions, occupied port 443, private Docker subnet overlap and Gemini API access.

## 4. Validate and flash StickS3

The patched Kai `secrets.h` matches the Relay. The preflash check rejects a private LAN IP **by default**: enabling it requires the explicit flag `--allow-private-ip`. Cloud/public builds must omit this flag.

```sh
cd ~/Grok-Amusant
python3 patches/sticks3-baseline/preflash.py \
  "$HOME/kai-build/firmware/src/secrets.h" \
  --expected-host 192.168.68.57 --allow-private-ip
python3 -m venv .venv-firmware
. .venv-firmware/bin/activate
python -m pip install platformio==6.2.0 -r patches/kai-v0.12.0-wifi-w1/tools/requirements.txt
platformio run -d ../kai-build/firmware -e sticks3
platformio run -d ../kai-build/firmware -e sticks3 -t upload
```

**PASS:** Preflash PASS, compile PASS, real USB flash verified. PlatformIO USB device permissions/ports vary; consult the [pinned firmware guide](../patches/amusant-sticks3-base-v0.12.0.md). The binary embeds your PSK/token; do **not** publish it.

## 5. BLE Wi-Fi provisioning and first voice

Follow [the BLE/PoP/QR setup guide](wifi-provisioning.md). With no saved profile, USB first boot shows **PoP text, not QR**; use a compatible ESP BLE provisioning phone app to submit Wi-Fi SSID and password. Later, B held for five seconds in Idle/Offline opens QR owner setup.

After the StickS3 joins Wi-Fi and reaches the local Relay, press and hold **front A**, ask a short question such as “What is a webhook?”, then release. **PASS:** Gemini Live speaks an audible answer on the StickS3. The successful Wi-Fi association alone is not an end-to-end PASS.

## 6. Add Grok SLOW after FAST works

The supported runtime **always selects `grok_callback` as its executor type**, but intentionally leaves task admission unavailable when the Routine and callback are not valid. To activate SLOW, you need an independently authenticated **public HTTPS callback** reachable by Grok Bot, a real Grok Routine HTTPS webhook, and a matching callback canonical URL and Host.

A possible test-only path is [Cloudflare Quick Tunnel](https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/do-more-with-tunnels/trycloudflare/), running from the Linux host:

```sh
cloudflared tunnel --url http://172.30.74.2:8645
```

This assumes the default `CALLBACK_API_CIDR` / `RELAY_CALLBACK_BIND_IP` from your private env and **rootful Linux Docker with host-to-bridge reachability**; verify the actual IP via `docker compose config` first. The callback listener binds its separate private Docker interface, **not 127.0.0.1**. Do not publish raw callback port 8645 to the internet.

The tunnel produces a temporary `https://….trycloudflare.com` URL. Set the private `AMUSANT_GROK_CALLBACK_URL=https://<exact-tunnel-host>/callback/results`, `AMUSANT_GROK_CALLBACK_TRUSTED_HOSTS=<exact-tunnel-host>`, and `AMUSANT_GROK_CALLBACK_SERVICE_PORT=443`. Add `AMUSANT_GROK_ROUTINE_URL` with your actual Routine endpoint; restart Relay. Send a test POST without a bearer token to that **public HTTPS callback URL**; **HTTP 401** indicates the receiver and Host mapping are reachable. Other status codes are not proof.

Then configure the Routine to accept task instructions and POST a final schema-valid result with `Authorization: Bearer <task result_token>` to that Callback URL. See [the Grok callback contract](self-hosting.md#5-wire-the-grok-bot-routine) and [the complete ARM64 cloud setup](build-your-own-jellii.md#7-first-fast-voice-test-then-slow).

**SLOW PASS:** delegated request → Grok Routine accepts → Callback receives one final result → Inbox shows unread → B reads it through Gemini Live → unread badge clears. Quick Tunnel is a disposable development ingress; restarting it may change the hostname. A stable cloud deployment belongs in the advanced guide.

## 7. Move to OCI/always-on later

See [ARM64 / OCI-style advanced deployment](build-your-own-jellii.md) for hosted ingress and Caddy. The **existing Amusant owner project already passed local FAST and OCI FAST/SLOW physical E2E**. This guide and the advanced reference remain subject to independent clean-room reproducibility acceptance.

**Security:** do not commit private `secrets.h`, the private env, PSK files, callback capabilities, logs with tokens, or credential-bearing binaries. For an internet-facing device route, retain TLS-PSK and avoid opening the raw callback listener.
