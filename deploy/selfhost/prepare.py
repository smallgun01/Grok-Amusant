#!/usr/bin/env python3
"""Create private matching Relay/StickS3 credentials without echoing secrets.

This does not deploy anything or contact a provider. Use on a trusted Linux host.
"""
from __future__ import annotations

import argparse
import getpass
import ipaddress
import platform
import os
from pathlib import Path
import re
import secrets
from urllib.parse import urlsplit
import warnings

REPO = Path(__file__).resolve().parents[2]
TEMPLATE = REPO / "deploy/oci-a1/grok-staging.env.example"
HOST_RE = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?")
OWNER_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}")
LAN_NETWORKS = tuple(ipaddress.ip_network(net) for net in (
    "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16",
))


def lan_ip(value: str) -> str:
    try:
        parsed = ipaddress.ip_address(value)
    except ValueError:
        raise ValueError("local mode requires an RFC1918 IPv4 address assigned to your host") from None
    if not isinstance(parsed, ipaddress.IPv4Address) or not any(parsed in net for net in LAN_NETWORKS):
        raise ValueError("local mode requires an RFC1918 IPv4 address assigned to your host")
    return str(parsed)


def local_platform() -> str:
    arch = platform.machine().lower()
    if arch in ("x86_64", "amd64"):
        return "linux/amd64"
    if arch in ("aarch64", "arm64"):
        return "linux/arm64"
    raise ValueError("local reference setup only supports native Linux amd64 or arm64")


def host_name(value: str) -> str:
    if not value or value != value.strip() or len(value) > 253:
        raise ValueError("provide a real, fully-qualified DNS hostname")
    labels = value.lower().split(".")
    if len(labels) < 2 or not all(HOST_RE.fullmatch(part) for part in labels):
        raise ValueError("provide a valid FQDN, not an IP address")
    forbidden = (".invalid", ".localhost", ".local", ".example", ".test", ".onion",
                 ".example.com", ".example.org", ".example.net")
    if value.lower().endswith(forbidden) or value.lower() in ("example.com", "example.org", "example.net"):
        raise ValueError("sample or local-only hostnames are not usable here")
    if any(label in ("your", "placeholder", "replace-me") for label in labels):
        raise ValueError("replace sample hostnames with a real DNS name")
    return value.lower()


def routine_url(value: str) -> str:
    parts = urlsplit(value)
    if parts.scheme != "https" or not parts.hostname or parts.username or parts.password or parts.query or parts.fragment:
        raise ValueError("Routine URL must be a plain HTTPS endpoint without embedded credentials")
    host_name(parts.hostname)
    if not parts.path or parts.path == "/":
        raise ValueError("Routine URL requires an explicit webhook path")
    return value


def safe_write(path: Path, content: str) -> None:
    with path.open("x", encoding="utf-8") as stream:
        os.chmod(path, 0o600)
        stream.write(content)


def prepare(*, private_dir: Path, firmware_dir: Path, device_host: str, callback_host: str | None = None,
            owner_id: str, gemini_api_key: str, grok_routine_token: str | None = None,
            grok_routine_url: str | None = None, mode: str = "cloud") -> dict[str, Path]:
    if mode not in ("local", "cloud"):
        raise ValueError("mode must be local or cloud")
    if mode == "local":
        device_host = lan_ip(device_host)
        if callback_host is not None or grok_routine_url is not None:
            raise ValueError("local FAST-first mode does not configure a public callback; add SLOW later")
    else:
        device_host = host_name(device_host)
        callback_host = host_name(callback_host or "")
    if not OWNER_RE.fullmatch(owner_id):
        raise ValueError("owner ID must be a simple bounded identifier")
    if not gemini_api_key.strip() or any(x in gemini_api_key for x in "\r\n\x00"):
        raise ValueError("Gemini API key is missing or malformed")
    if grok_routine_token is None:
        grok_routine_token = secrets.token_urlsafe(32)
    if not grok_routine_token.strip() or any(x in grok_routine_token for x in "\r\n\x00") or len(grok_routine_token) < 12:
        raise ValueError("Grok Routine token is missing or malformed")
    if grok_routine_url is not None:
        routine_url(grok_routine_url)
    private_dir = private_dir.expanduser().resolve()
    firmware_dir = firmware_dir.expanduser().resolve()
    if any(char in str(private_dir) for char in "\r\n\t #$'"):
        raise ValueError("choose a private directory path without spaces, '#' or '$'")
    if private_dir == REPO or REPO in private_dir.parents:
        raise ValueError("private configuration must be outside the Amusant repository")
    if private_dir.exists():
        raise FileExistsError("private output directory already exists; refusing to overwrite secrets")
    if not (firmware_dir / "src/secrets.example.h").is_file():
        raise FileNotFoundError("pinned Kai patch must be applied first (missing firmware/src/secrets.example.h)")
    target = firmware_dir / "src/secrets.h"
    if target.exists():
        raise FileExistsError("firmware secrets.h already exists; refusing to replace it")
    if firmware_dir == REPO or REPO in firmware_dir.parents:
        raise ValueError("build firmware outside the Amusant repository")

    psk = secrets.token_hex(32)
    device_token = secrets.token_urlsafe(32)
    identity = "stick-01"
    paths = {
        "gemini_api_key": private_dir / "secrets/gemini_api_key",
        "device_token": private_dir / "secrets/device_token",
        "device_psks": private_dir / "secrets/device_psks",
        "grok_routine_token": private_dir / "secrets/grok_routine_token",
        "env": private_dir / "private.env",
        "firmware": target,
    }
    updates = {
        "RELAY_IMAGE": "amusant-relay:selfhost",
        "EGRESS_PROXY_IMAGE": "amusant-egress:selfhost",
        "AMUSANT_CLIENT_ID": owner_id,
        "AMUSANT_GROK_ROUTINE_URL": grok_routine_url or "https://routine.example.invalid/webhook",
        "AMUSANT_GROK_CALLBACK_URL": (f"https://{callback_host}:9443/callback/results"
                                         if mode == "cloud" else "https://callback.example.invalid/callback/results"),
        "AMUSANT_GROK_CALLBACK_TRUSTED_HOSTS": (callback_host if mode == "cloud" else "callback.example.invalid"),
        "AMUSANT_GROK_CALLBACK_SERVICE_PORT": ("9443" if mode == "cloud" else "443"),
        "OCI_DATA_DIR": str(private_dir / "data"),
        "GEMINI_API_KEY_FILE": str(paths["gemini_api_key"]),
        "DEVICE_TOKEN_FILE": str(paths["device_token"]),
        "DEVICE_PSKS_FILE": str(paths["device_psks"]),
        "GROK_ROUTINE_TOKEN_FILE": str(paths["grok_routine_token"]),
    }
    lines = []
    found = set()
    for line in TEMPLATE.read_text(encoding="utf-8").splitlines():
        name, sep, original = line.partition("=")
        if sep and name in updates:
            lines.append(f"{name}={updates[name]}")
            found.add(name)
        else:
            lines.append(line)
    if found != set(updates):
        raise ValueError("environment template changed; refusing to create incomplete configuration")
    if mode == "local":
        # Not part of the OCI template; consumed by compose.local.yaml only.
        lines.extend((f"HOST_LAN_IP={device_host}", f"AMUSANT_DOCKER_PLATFORM={local_platform()}"))

    header = ("#pragma once\n"
              f'#define RELAY_HOST "{device_host}"\n'
              "#define RELAY_PORT 8765\n"
              "#define RELAY_TLS_PORT 443\n"
              f'#define KAI_PSK_IDENTITY "{identity}"\n'
              f'#define KAI_PSK_HEX "{psk}"\n'
              f'#define KAI_DEVICE_TOKEN "{device_token}"\n')
    private_dir.mkdir(parents=True, mode=0o700)
    (private_dir / "secrets").mkdir(mode=0o700)
    (private_dir / "data").mkdir(mode=0o700)
    (private_dir / "data/relay").mkdir(mode=0o700)
    safe_write(paths["gemini_api_key"], gemini_api_key.strip() + "\n")
    safe_write(paths["device_token"], device_token + "\n")
    safe_write(paths["device_psks"], f"{identity}:{psk}\n")
    safe_write(paths["grok_routine_token"], grok_routine_token.strip() + "\n")
    safe_write(paths["env"], "\n".join(lines) + "\n")
    if mode == "cloud":
        paths["caddy"] = private_dir / "Caddyfile"
        safe_write(paths["caddy"], (
        "{\n"
        "    auto_https disable_redirects\n"
        "}\n\n"
        f"{callback_host}:9443 {{\n"
        "    tls {\n"
        "        issuer acme {\n"
        "            disable_tlsalpn_challenge\n"
        "        }\n"
        "    }\n"
        "    reverse_proxy 172.30.74.2:8645\n"
        "}\n"
        ))
    safe_write(paths["firmware"], header)
    return paths


def secure_prompt(message: str) -> str:
    with warnings.catch_warnings():
        warnings.simplefilter("error", getpass.GetPassWarning)
        try:
            return getpass.getpass(message)
        except getpass.GetPassWarning as exc:
            raise RuntimeError("terminal cannot hide secret input; no credentials were sent") from exc


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--device-host", required=True, help="LAN IPv4 with --local; real DNS hostname for cloud")
    parser.add_argument("--local", action="store_true", help="LAN FAST-first mode; no public DNS or callback required")
    parser.add_argument("--callback-host", help="real DNS hostname for cloud HTTPS callback port 9443")
    parser.add_argument("--private-dir", type=Path, required=True, help="new, non-repository private directory")
    parser.add_argument("--firmware-dir", type=Path, required=True, help="patched Kai firmware directory")
    parser.add_argument("--routine-url", default=None, help="optional Grok Routine HTTPS webhook URL; omit for FAST-first")
    parser.add_argument("--owner-id", default="owner-1")
    args = parser.parse_args()
    try:
        if args.local:
            lan_ip(args.device_host)
            local_platform()
            if args.callback_host is not None or args.routine_url is not None:
                raise ValueError("--local FAST-first does not use --callback-host or --routine-url")
        else:
            host_name(args.device_host)
            host_name(args.callback_host or "")
            if args.routine_url is not None:
                routine_url(args.routine_url)
        gemini = secure_prompt("Gemini API key (hidden): ")
        routine_token = (None if args.local else
                         secure_prompt("Grok Routine bearer token (hidden; empty = generate a new one): "))
        result = prepare(private_dir=args.private_dir, firmware_dir=args.firmware_dir,
                         device_host=args.device_host, callback_host=args.callback_host,
                         owner_id=args.owner_id, gemini_api_key=gemini,
                         grok_routine_token=routine_token or secrets.token_urlsafe(32),
                         grok_routine_url=args.routine_url, mode="local" if args.local else "cloud")
    except (OSError, ValueError, RuntimeError) as exc:
        parser.exit(2, f"Configuration not generated: {exc}\n")
    print(f"Private configuration created at {result['env']} and firmware secrets.h (values not printed).")
    print("Next: check generated files, set Secret UID/GID/mode and SQLite ownership, then run preflash validation.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
