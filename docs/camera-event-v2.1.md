# Camera event 2.1

Version 2.1 retains v2 ordered media, passage identity, timestamps and normalized
edge boxes. It requires one additional `camera_context` object. v1 and v2 remain
strict and unchanged; a v2 request with this field is rejected.

`camera_context` contains:

- `detection_polygon`: zero or 3–32 normalized `[x,y]` points in the original frame.
- `configuration_revision`: immutable capture configuration digest.
- `configuration_source`: `capture_snapshot`, `backfill_current_settings`, or
  `legacy_unknown`. A backfill must not pretend current settings were captured
  when the historical video was recorded.
- `model_identity`: `{name, sha256, train_class_id, input_size}` for the local model.
- `parent_event_id`: nullable previous local event ID when a historical event is
  explicitly resubmitted with new context. Never mutate a finalized input.
- `training_intervals`: original capture-relative `{start_seconds, end_seconds,
  label}` ranges. Labels are `unreviewed_pre_detection` or
  `unreviewed_train_candidate`; neither is verified ground truth.

