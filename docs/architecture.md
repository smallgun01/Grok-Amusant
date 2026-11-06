# Architecture and data flow

```text
Voice:   StickS3 <-- TLS-PSK / WSS --> Relay <-- Live --> Gemini
Task:    Relay --> SQLite admission --> Egress proxy --> Grok Routine
Result:  Grok Routine --> separate callback ingress --> Relay --> SQLite Inbox
Read:    StickS3 --> Relay --> Inbox --> voice response
```

The Relay owns authenticated device sessions and stores task state in SQLite. A delegated task is acknowledged after durable admission; an uncertain submission is not automatically replayed. The callback receiver authenticates a task-bound capability and handles duplicate results. The device can retrieve saved results from its own Inbox.

The egress proxy controls outbound connections. Device and callback ingress must be provided and secured separately; the Compose sample does not expose ports. Runtime startup requires an explicit `grok_callback` executor. Compatibility code present in the tree is not a public support promise.

For configuration boundaries see [Configuration](configuration.md); for tests and builds see [Quick start](quickstart.md).
