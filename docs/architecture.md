# Architecture and data flow

```mermaid
flowchart LR
    device["StickS3 device"] <-->|"TLS-PSK / WSS"| relay["Relay"]
    relay <-->|"FAST voice"| gemini["Gemini Live"]
    relay <-->|"Task state and Inbox"| store[("SQLite")]
    relay -->|"SLOW task submission"| proxy["Egress proxy"]
    proxy --> routine["Grok Bot Routine"]
    routine -->|"Callback result"| ingress["Separate callback ingress"]
    ingress -->|"Authenticated callback"| relay
```

The Relay owns authenticated device sessions and stores task state in SQLite. A delegated task is acknowledged after durable admission; an uncertain submission is not automatically replayed. The callback receiver authenticates a task-bound capability and handles duplicate results. The device can retrieve saved results from its own Inbox.

The egress proxy controls outbound connections. Device and callback ingress must be provided and secured separately; the Compose sample does not expose ports. Runtime startup requires an explicit `grok_callback` executor. Compatibility code present in the tree is not a public support promise.

For configuration boundaries see [Configuration](configuration.md); for tests and builds see [Quick start](quickstart.md).
