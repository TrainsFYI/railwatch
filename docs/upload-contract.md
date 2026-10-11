# Legacy upload adapter

The production app uses frontend-approved device enrollment and the [multipart device protocol](device-protocol.md). The adapter below remains for compatibility tests and custom legacy integrations; it is not the account-linked TrainsFYI gateway.

# Proposed camera upload contract (not implemented by TrainsFYI)

The legacy adapter has no default upload endpoint. The user may save an API key without enabling uploads. An explicitly configured HTTPS endpoint and nonempty API key activate the worker. Camera capture works independently.

The client sends a multipart POST to the configured URL:

- `Authorization: Bearer <camera API key>`
- `Idempotency-Key: <stable local sighting UUID>`
- `video`: an H.264 MP4 without audio, 960×540 at 10 fps
- `metadata`: JSON string with `schema_version: 1`, `id`, `name`, `latitude`, `longitude`, `bearing` (clockwise from north), `field_of_view` (degrees), `capture_fps`, `width`, `height`, `started_at` and `ended_at` (Unix UTC seconds), `confidence` (0–1), and `sha256` of the exact MP4.

Camera URLs and credentials never enter sighting metadata. Server replies must be HTTP 200, 201 or 202 with JSON `{"id":"server-sighting-id"}`. An HTML success page, redirect, empty response or auth error is not an acknowledgement. `202` means durable server acceptance, not completed ML processing.

The server must deduplicate `Idempotency-Key` and return the same receipt on retry. Client timeouts can occur after server acceptance. Every unsuccessful attempt is persisted and retried with capped exponential backoff (30s–1h); manual retry resets its next-attempt time. The client does not follow redirects with credentials. On restart, in-flight uploads return to pending.

No account validation or remote processing-status endpoint is assumed. The dashboard distinguishes local queued clips and server-accepted uploads. It does not claim the key is verified before a successful upload, or that processing finished. Later integration needs server-side authentication, upload limits, idempotency storage, receipt semantics and a processing-status API agreed here first.
