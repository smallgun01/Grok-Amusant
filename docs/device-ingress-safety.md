# Device ingress boundaries

| Profile | Source visibility and existing controls | Remaining responsibility |
| --- | --- | --- |
| LAN `compose.local.yaml` | Host networking sees the original source before Docker forwarding. The TCP forwarder does not pass that identity to Relay. Relay applies a 15-second TLS handshake timeout. | The forwarder limits all active sessions, including authenticated ones, and times out waiting for the first byte. Restrict TCP 443 to the LAN at the host/router. |
| Advanced `compose.ports.yaml` | Host firewall sees source before Docker port forwarding; Relay peer identity depends on the host/Docker network path and is not a portable source identity. Relay applies the TLS handshake timeout. | This bypasses the LAN forwarder and its limits. Configure host/edge global and source-aware connection/rate limits before any Internet exposure; preserve Docker forwarding and SSH access. |
| Legacy OCI socket proxy | Host/edge sees the source before proxying. Relay sees a proxy-side peer, not the original client. The accepted legacy design uses host-side source controls and socket-proxy connection limits; Relay still bounds the TLS handshake. | This source reference does not inspect or change the existing host. Exact effective and persisted host configuration is owner-managed. Callback remains separately private. |

For LAN, `DEVICE_INGRESS_MAX_CONNECTIONS` defaults to 32 (range 1–1024), and `DEVICE_INGRESS_FIRST_BYTE_TIMEOUT` defaults to 10 seconds (range 1–300). Set them in the private Compose environment if needed. A full budget closes additional connections immediately; slots return when a connection ends. The deadline applies only until the first client byte, not throughout an authenticated voice session. A client can send a byte without authenticating; Relay's existing handshake timeout still applies after forwarding begins. The forwarder cannot identify authenticated sessions, rate-limit reliably by source, or prevent kernel accept-queue exhaustion.

## Sanitized OCI architecture reference

The established legacy route is host TCP 443 → socket-activated `systemd-socket-proxyd` → private Relay TCP 8443. A minimal reference for a future separately approved deployment is below; placeholders deliberately are not usable configuration. Use matching socket/service unit names and an actual private Relay address selected by the operator. Do not publish the callback listener or egress proxy.

```ini
# example .socket (reference only)
[Socket]
ListenStream=<HOST_PRIVATE_INTERFACE_ADDRESS>:443
Accept=no
Backlog=64
Service=amusant-device-ingress.service

# example .service (reference only)
[Service]
ExecStart=/usr/lib/systemd/systemd-socket-proxyd --connections-max=31 --exit-idle-time=120s <PRIVATE_RELAY_ADDRESS>:8443
```

The proxy limit is global; `--exit-idle-time` is the proxy service's idle lifetime, not a client idle timeout. The legacy source renderer accounts for systemd version-specific connection-limit behavior: validate the desired effective cap for the installed version instead of assuming the option gives an exact count. The historical accepted host policy used source-aware SYN and parallel-connection controls; preserve changing client addresses rather than requiring a fixed device IP. These figures are a reference, not a statement of current runtime state. Before a future cutover, independently review host firewall/NSG and Docker paths, SSH and callback isolation, effective versus persisted settings, and version-specific systemd behavior. Do not save an entire live Docker-modified firewall ruleset as a persistence shortcut.

For rollback, restore the previously reviewed host units and firewall configuration through the operator's existing change process; restore the prior Compose image and environment for LAN. Keep a working SSH access path and do not change active ingress during source review. Source tests do not establish production or physical-device acceptance. The separate B1 recovery limitation remains tracked in issue #6.
