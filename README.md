# Amusant

Amusant is an experimental push-to-talk device relay. Gemini Live handles the fast voice path, while Grok Routine and callback handle delegated tasks. SQLite stores tasks and Inbox results; StickS3 devices connect through TLS-PSK and WSS. The candidate is being prepared for release and has not been approved for production deployment.

## Offline quickstart

Use Python 3.13 and [uv](https://docs.astral.sh/uv/). From the repository root:

```sh
uv sync --locked --extra hardware-opus --group dev
uv run --locked python -m unittest relay.tests.test_grok_callback relay.tests.test_inbox fast_voice.tests.test_contract
```

These tests use fake providers, synthetic credentials, and local resources; they do not require Gemini or Grok credentials. The complete offline test selection, including device security and preflash checks, is in `.github/workflows/offline.yml` and can be run manually in GitHub Actions.

## Architecture and configuration

The device sends audio to the Relay, which uses the Gemini Live provider for fast responses. Slow tasks are recorded in SQLite and sent through the egress proxy to the configured Grok Routine; a separate callback listener stores returned results in the Inbox. The supported slow executor is `grok_callback`; missing or unsupported profiles fail closed.

`deploy/oci-a1/grok-staging.env.example` and `compose.grok.yaml` illustrate configuration, not a ready-to-deploy service. Image references and service URLs are intentionally invalid placeholders. The example uses local file mounts for secrets and a persistent SQLite directory; replace placeholders only in a private configuration. The Compose file does not publish device or callback ports: an appropriate private device ingress and authenticated callback ingress must be designed and verified separately. Never commit real credentials, hostnames or private deployment files.

Manual CI workflows build Relay and egress images separately for `amd64` or `arm64` and apply the firmware patch to pinned Kai before host tests and a compile-only StickS3 build. See [firmware instructions](patches/amusant-sticks3-base-v0.12.0.md). A successful build does not prove production deployment, provider integration, or physical device operation.

## Licenses and status

Third-party notices and future firmware binary obligations are described in [NOTICE.md](NOTICE.md). The project-wide Amusant license remains pending an owner decision; this document does not grant one. [Traditional Chinese README](README.zh-TW.md).
