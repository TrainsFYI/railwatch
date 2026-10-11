# TrainsFYI camera protocol

Railwatcher contains only capture, local detection, review and device upload
code. Backend processing/training code and internal service credentials are not
part of this repository. Any locally distributed model is accessible to the
camera owner.

The default API is `https://api.trains.fyi/api/v5/cameras/`. HTTPS is required.

- `POST pairing/`: `{name, location:{latitude,longitude}}` returns `session_id`,
  private `pairing_secret`, eight-character `code`, `approval_url`, `expires_at`
  and `poll_interval:5`. Display the approval link as a QR code.
- A signed-in invited owner reviews and approves through the TrainsFYI frontend.
  Approval sessions expire after ten minutes.
- `POST pairing/{session_id}/exchange/`: `{pairing_secret}`. Poll no faster than
  five seconds. 202 means pending, 429 throttled, 410 expired. Success returns
  a unique `credential` and server-assigned `camera.id` and `camera.device_id`.
- Store credential, pairing secret and resumable session URLs in mode-0600
  files owned by the service user. Never expose these through the local API,
  logs, QR code or shared configuration. Camera connection passwords stay local.
- Use `Authorization: Bearer <device credential>` for `heartbeat/`, `uploads/`
  and `events/{local_event_id}/finalize/` only. No Firebase token or internal
  shared key is needed by the device.

## Immutable multipart events (schema 2.0.0)

POST `uploads/` with the following exact fields. Unknown fields are rejected.

`schema_version`, `event_id`, `created_at`, `observed_at`, `source`, `capture`,
`media`, `camera`, `edge_observations`, `passage`.

`event_id` is a persistent local passage UUID, independent of file boundaries.
`source` declares `source_type:"edge_camera"`, `source_id`, `camera_id`; the
server replaces source identity and camera registration using verified device
records. Both camera IDs must match the device registration. Never send a user
ID, camera password or backend key.

`capture` has `kind` (`completed_passage` or `observation`), UTC `started_at`,
`ended_at`, `timebase` (`utc` or `presentation_timestamp`) and `frame_timestamps`.
Each optional timestamp anchor is `{frame_index,pts_seconds,source_timestamp}`.
Optional fields are `triggered_at`, `timebase_numerator`, `timebase_denominator`.

`media` is `{parts:[{index,started_at,ended_at,video}],poster_frame:null}`.
Parts use consecutive indexes starting at zero and ordered UTC starts. Their
intervals may overlap; cloud analysis trims overlap. Each video declaration is
`{uri,sha256,size_bytes,mime_type,duration_seconds}`. URI is a local reference on
submission and becomes a server-assigned GCS URI. Digests are lowercase SHA-256.
Durations must agree with intervals within two seconds. No more than 100 parts.
An optional poster uses `{uri,sha256,size_bytes,mime_type}`. Video/poster media
must be non-empty, at most 50 GiB/25 MiB respectively.

`camera` has exactly `camera_id,name,location,heading_true_deg,
heading_accuracy_deg,calibration_revision,rail_mapping`. Unavailable values are
null; the server replaces registration/calibration. The client cannot approve
railway direction, speed or publication.

`edge_observations` contains up to 10,000 entries:
`{kind,pts_seconds,bbox,state,confidence}`. `kind` is train/locomotive/railcar/unknown;
state is moving/stationary/unknown. Boxes are normalized `[left,top,right,bottom]`
and must have positive area; confidence is 0–1. Time is relative to capture start.

`passage` is `{detected_started_at,detected_ended_at,complete,incomplete_reason,
model_version,configuration_revision,origin}`. Detected bounds exclude pre/post
padding. `origin` is `confirmed_live` or `owner_submitted_archive`. Incomplete
captures require a reason; complete captures require null and cannot contain
recording gaps. Historical discoveries do not upload without explicit submission.

## Delivery and retries

The response includes server `event_id`, `local_event_id`, `status` and `uploads`
keyed by `part_0000`, `part_0001`, etc., plus optional `poster_frame`. Each artifact
has `uri`, `uploaded`, and, when needed, private `upload_url`. PUT bytes directly
to the GCS resumable session, without device Authorization headers. Send
`Content-Range`, recover progress with an empty `bytes */<size>` PUT, and honor
308/Range. Persist session state; renew expired sessions by repeating reservation.

POST finalization with the original local UUID after all parts upload. Only
`status:"accepted"` acknowledges durable, digest-verified storage. Analysis is
asynchronous. Retain local media until this acknowledgment. Identical retries
are idempotent; changed content under an existing identity returns 409. 401 means
revoked access, 429 daily quota (2 GiB by default), 410 expired retention, 503
retryable service failure. These responses must never silently discard footage.

The original strict single-video v1 ingest contract remains supported for other
producers. It is an internal service contract, not a public device credential API.
