# RailWatch

[TrainsFYI/railwatch](https://github.com/TrainsFYI/railwatch) is the source repository.
Existing `railwatcher` commands, service names, and data paths remain compatible.

A Raspberry Pi lookout for trains.fyi. Connect a USB webcam or local RTSP camera, place it on a satellite map, and record passing trains with a local train detector.

## What works

- Local browser wizard: camera selection, live preview, satellite position, railway overlay, draggable pin, bearing and field-of-view cone.
- Camera preview, settings, history and clips available to any device on the local network.
- One FFmpeg camera connection, independent local inference, repeated-positive confirmation, pre/post-roll MP4 recordings and a low-bitrate 24-hour rolling footage archive.
- Day-and-night inference with the existing TrainsFYI single-class YOLOv8 model, exported to ONNX and run using OpenCV DNN. PyTorch is not needed on the Pi.
- SQLite sightings history, local video playback, persisted upload retries and bounded storage. Uploaded clips are pruned oldest first; pending clips are never automatically discarded. Capture pauses when pending clips consume the storage budget.
- systemd installer, service restart recovery, camera reconnection, and local dashboard with real device/queue status.

**Account-linked camera pilot:** choose Connect to TrainsFYI in setup or Settings, then approve the camera through your TrainsFYI account in a browser. New camera views are reviewed before their observations contribute to the public network. Unpaired cameras record locally. Confirmed passages upload privately through the [device protocol](docs/device-protocol.md); cloud processing and models remain in the private backend.

## Install on a Raspberry Pi

### Recommended hardware

For one camera, start with:

- **Raspberry Pi 5, 4 GB** (8 GB if you also plan to run other services). This is a suggested baseline; inference speed and temperature still need checking with your camera/model. Use the official 27 W USB-C power supply and an active cooler or fan case for continuous inference. RailWatch supports 64-bit Raspberry Pi OS; the Pi 5 has a 64-bit quad-core CPU and USB 3 for camera/storage peripherals. See the [Pi 5 specifications](https://www.raspberrypi.com/products/raspberry-pi-5/).
- **Wired Ethernet for the Pi** where possible, especially when the camera streams over Wi-Fi. Use a good quality 64 GB or larger microSD card for the OS; an external USB 3 SSD is preferred for video storage and sustained use.
- **One camera with a stable 1280×720 or higher stream.** A USB UVC webcam can connect directly to the Pi. For an outdoor trackside view, choose a weather-rated camera with night/IR support and a local RTSP stream; keep the camera's power and network connection stable.

**Recommended budget camera: Wyze Cam v3.** Current production firmware includes RTSP support; Wyze announced the production rollout on February 2, 2026, with the RTSP setting available in the Wyze app ([firmware announcement](https://forums.wyze.com/t/wyze-firmware-updates-2-2-2026/340669)). Update the Wyze app to version 3.9 or later and the camera firmware, then enable RTSP in the camera's advanced settings, set its RTSP username and password, and paste the generated stream URL into RailWatch. Wyze rolls firmware out gradually, so update the app and camera before installation if the RTSP setting is missing. The v3 has night vision and needs its own USB power supply; it does not provide PoE ([Wyze v3 FAQ](https://support.wyze.com/hc/en-us/articles/7816811330331-More-Wyze-Cam-v3-FAQs)).

### Install with the guided Pi wizard

Start with **Raspberry Pi OS Lite (64-bit)** on a Raspberry Pi 5. Use [Raspberry Pi Imager](https://www.raspberrypi.com/software/) to write the card, set a username/password and network, and enable SSH. Boot the Pi, then open a terminal or SSH session on it.

Run this one command **on the Pi**:

```sh
curl -fsSL https://raw.githubusercontent.com/TrainsFYI/railwatch/main/install.sh | bash
```

The interactive installer explains the requirements, asks before installing, downloads the newest official GitHub release (including pilot prereleases), checks its SHA-256 digest, and installs RailWatch with automatic startup and the update helper. It asks for your Pi's sudo password if needed. No repository checkout or model file is required to start setup.

After installation, open the printed address, usually **http://railwatcher.local:8787**, on a phone or computer on the same network. The browser wizard walks through your camera and live preview, railway position and landmarks, detection area, and trains.fyi account connection. Download the offered detector in Settings if one is not installed. Check that detection is watching, then verify your first train in Detections.

Keep the dashboard on a trusted local network; it has no login. Do not forward its port on your router. You can return to Settings later to change your camera or account connection.

Existing Pi installations should run the command above **once** to add the updater. Settings, pairing, recordings, models and pending uploads stay in `/var/lib/railwatcher` and are preserved. The original source installer remains available with `sudo bash scripts/install.sh` for development or a supplied ONNX model.

```sh
sudo systemctl status railwatcher
sudo journalctl -u railwatcher -f
```

### Software releases and updates

Published versions are on [GitHub Releases](https://github.com/TrainsFYI/railwatch/releases). **0.1.1 is our first prerelease.** Pilot installations include prereleases when checking for updates.

RailWatch checks GitHub at startup when due, then once every 24 hours, even when the dashboard is closed. The last check survives restarts. An unavailable network does not stop capture or discard a previously discovered update.

When a newer installable release is available, **Please update** appears in the top navigation. Open it to see the installed/new versions and release notes, then choose **Update now**. The Pi downloads a verified release into a separate Python environment while monitoring continues, waits for an idle recording boundary, and briefly restarts. If the new version fails its health check, the updater switches back to the previous environment and reports the failure. Keep power connected during the update.

Docker and source-run installations show the same notice but do not give the web app access to Docker or root privileges. For Docker, preserve your data/model volumes, fetch the release tag, then rebuild and recreate the container:

```sh
git fetch origin --tags
git checkout v0.1.1
docker compose up -d --build
```

For later releases, substitute their published tag. Commit or save local source edits before changing versions. Developer virtual environments can install the checked-out version with `pip install .` and restart their process. To enable one-click Pi updates, use the guided installer above.

For offline development or externally managed deployments, `RAILWATCHER_RELEASE_CHECKS=off` disables background checks.

The dashboard can request only a version from the fixed `TrainsFYI/railwatch` repository. A separate root-owned systemd helper independently verifies the published release, bundle URL, size and GitHub SHA-256 digest. It does not accept shell commands or arbitrary download URLs, and it does not modify camera data. Update activity is available with `journalctl -u railwatcher-update`.


## Model export

Export on a development machine, then copy the resulting ONNX file and JSON manifest to the Pi. Only load a trusted checkpoint: PyTorch checkpoints can contain executable code.

```sh
python3.12 -m venv .venv
.venv/bin/pip install -e '.[dev,export]'
.venv/bin/python scripts/export_model.py \
  /path/to/yolov8n-trains.pt
```

This accepts either the TrainsFYI single-class checkpoint or a COCO YOLOv8 checkpoint with a named train class. It exports a fixed 640×640 graph without NMS, validates the output with OpenCV, and writes class mapping and SHA-256 provenance to `models/train.json`. The source checkout is not modified. Model binaries are ignored by git. Deployment needs the ONNX file and its JSON manifest; training/export packages stay on the development machine. An already-installed Pi expects `/opt/railwatcher/models/train.onnx`. Missing or incompatible models are visible on the dashboard, not silently replaced with a motion detector.

Ultralytics also documents [NCNN as a Raspberry Pi optimization](https://docs.ultralytics.com/guides/raspberry-pi/). This first client uses OpenCV/ONNX for a smaller runtime integration; actual inference speed and CPU/thermal load still need benchmarking on the target Pi. Review the existing model's licensing and the upstream export tool's terms before distributing model binaries.

## Run locally

FFmpeg must be installed and on `PATH`.

```sh
python3.12 -m venv .venv
.venv/bin/pip install -e '.[dev]'
.venv/bin/railwatcher
# open http://127.0.0.1:8787
```

USB capture uses Linux V4L2. On macOS, use RTSP for real capture; the web app and tests run without a connected camera. To listen on the LAN use `--host 0.0.0.0`. Other options: `--port`, `--data`, `--model`; service equivalents are `RAILWATCHER_DATA` and `RAILWATCHER_MODEL`. Run **one application process** per camera; do not add Uvicorn workers.

## Run with Docker Compose

The Compose service builds a small runtime image with FFmpeg, runs as UID 10001, and stores configuration, database and clips in a persistent data directory. Model weights stay outside the image. Prepare writable data and model directories, then start it:

```sh
mkdir -p data models
sudo chown 10001:10001 data
docker compose up -d --build
docker compose logs -f railwatcher
```

Compose binds to `127.0.0.1:8787` by default. To reach it from another device, set `RAILWATCHER_BIND_IP` in a local `.env` file to an address trusted clients can reach (for example, the host's Tailscale IP). Only bind to `0.0.0.0` on a trusted LAN: the app has no login, so do not expose it to the public internet. `RAILWATCHER_DATA_DIR` and `RAILWATCHER_MODEL_DIR` can point to persistent host paths; the model directory is mounted read-only.

```sh
.venv/bin/pytest
.venv/bin/ruff check .
node --check railwatcher/static/app.js
npm ci
npx playwright install chromium
npm run test:browser
```

The browser smoke test launches a disposable local server, exercises the complete wizard and mobile dashboard, and writes screenshots to `artifacts/`. Its fake camera credentials and temporary data are isolated from your real configuration. The Python suite also exercises actual FFmpeg capture and MP4 encoding using synthetic video.

## Recording behavior and limits

The detector keeps 12 seconds of recent frames in RAM for event pre-roll, confirms three positive samples within five seconds, and records until 15 seconds after the last positive. Preview, inference and event clips use 960×540 at 10 fps; inference samples the latest frame independently, at most twice per second. In parallel, the camera feed is encoded at 640×360 and 5 fps into two-minute H.264 files, retained for up to 24 hours. The 350 kbit/s bitrate ceiling uses about 3.8 GB per day at the maximum rate. The Live page shows recent events; use Detections to open individual recordings. The current segment is finalized at each segment boundary, so the newest footage can take up to two minutes to appear. A gap in camera capture is not filled with fabricated footage.

Recordings longer than five minutes are split into clips with overlapping pre-roll. A stationary train can keep the detector active; this version detects train presence rather than estimating movement or counting individual trains. Dashboard totals count recording events, including interrupted attempts, not unique physical trains. Restart/disconnection closes the current recording where possible; footage while disconnected cannot be recovered.

The detector runs at night, but the installed model has not been validated here on nighttime/IR footage. Camera illumination, IR reflections, passing cars and rain need field testing and likely more training data. There is no promise of complete recall, train identity or direction-of-travel inference. Camera bearing describes where the lens faces, not where the train is headed.

Storage is a recording budget, with a conservative reserve for the next maximum-length clip and 200 MB of free-disk headroom. The rolling archive removes footage older than 24 hours and sheds its oldest segments if free disk space drops below 1 GB; the dashboard reports the footage currently available. A 64 GB microSD card is the practical minimum for the OS, event clips and rolling archive; a USB 3 SSD is recommended for sustained camera recording. Metadata is retained after uploaded videos are pruned. Queued clips remain local until accepted. There is no automatic deletion of unsubmitted sightings. Pausing detection also pauses camera capture and the rolling archive. Audio is never captured.

The wizard bundles Leaflet locally; satellite imagery and highlighted railways are online services. Imagery is supplied by Esri; rail tiles/data by OpenRailwayMap/OpenStreetMap, with attribution shown on the map. The default public rail tiles are for this development setup. Before public/commercial distribution, provide a permitted or self-hosted rail tile endpoint according to [OpenRailwayMap's usage terms](https://wiki.openstreetmap.org/wiki/OpenRailwayMap/API); default endpoints are in `railwatcher/static/app.js`. Manual coordinate entry continues to work without tiles. Browser geolocation generally needs HTTPS except on localhost.

## Highlightable landmarks

In Camera settings, choose **Add landmark**, enter a label, and drag a box on the live image. Select a saved landmark to rename it or draw a replacement box. Remove unwanted landmarks and use **Save settings** to persist changes. Boxes use image-relative coordinates and appear on the live dashboard. Up to 32 landmarks can be saved. Landmarks annotate fixed features in the camera image.

## Train detection and archive review

**Review footage → Train model detections** is the default. Archive analysis uses the same weights, saved area, confidence threshold and confirmations within five seconds as live recording, with pre/post-roll for review windows. Archive inference samples once per second. Existing footage is analysed newest first, and results invalidate when the model or relevant settings change. Archive analysis does not create sightings or upload clips automatically.

Model export supports both a single-class checkpoint and standard COCO YOLOv8 checkpoints. The accompanying `train.json` identifies the train class (COCO class 6), class count, provenance hashes and `focus_on_area`. With area focus enabled, inference crops to the saved area plus surrounding context before letterboxing, then maps detection centres back to the original image for polygon filtering. Keep the JSON alongside its ONNX model; multi-class models without the correct manifest are rejected. Legacy single-class models remain supported without a manifest.

The server-261 VIA passage on 2026-10-10 exposed poor recall in the custom YOLOv8n checkpoint. YOLOv8m with track-area focus recognises the recorded passage at the existing 0.55 confidence threshold. This is a regression example, not a guarantee of recall across all trains, lighting, cameras or weather. Red-flash background indexing is paused; saved landmarks remain available.

Account-linked camera uploads use the public v2.1 camera context contract. New
installations retain up to 120 seconds before local confirmation. Those ranges
are candidate negative training context requiring review, not automatic ground
truth labels. Captured polygons, model hashes and configuration revisions travel
with the clip. Existing pending v2.0 uploads retain their original retry identity.

### Nighttime training review

Settings → Recording & storage → Night review can retain nighttime archive
segments beyond the normal 24-hour buffer. Choose the installation's IANA
timezone, local start/end hours, retention hours and a separate storage budget.
The oldest retained recordings expire when either limit is reached. Capture
leaves 1 GB of disk headroom and never prunes pending upload media for this
review budget. Review footage → All footage includes these recordings, labeled
“Night review”; they can be played, marked, and explicitly submitted like other
historical footage. Retention does not confirm a train and does not enqueue an
automatic upload. Disabling collection stops new retention; existing review
recordings expire according to the configured limits.


### Camera setup and navigation

The setup guide walks through the camera connection, camera position and viewing
cone, two matching rail landmarks, the train detection area, optional account
connection, and a final review. Choose fixed spots on the same straight rail for
landmarks A and B, then mark each spot in both the live image and satellite map.
Each matched pair is saved locally; reloading the same browser tab resumes setup.
The matched points establish the rail axis; speed still requires verification.

Outline the full space a train occupies in the picture with at least three
points, or explicitly choose the whole image. Drawing uses the camera's native
aspect ratio so the saved outline stays aligned. Recalibrate when moving or
zooming the camera.

Desktop navigation provides Live, Detections, and Settings. On smaller screens these become a horizontal navigation bar. Existing
configured devices open their overview without repeating setup.


Detections and the overview's five most recent events share a table. Select an
entry to inspect its recording and details. Detections below 80% confidence show
Needs review until the user confirms or dismisses them. Manual sightings count
as confirmed. Review decisions are retained locally without rewriting the
original capture confidence or upload history. Recordings that have expired
remain visible as events, with an explanation that their video is unavailable.

Detection recordings use a dedicated player with an amber detection interval,
scrubbing, five-second skips, speed selection, and fullscreen. Opening a clip
starts playback three seconds before the saved detection interval; Play detection
returns to that point. Saved passage timestamps are clipped to the recording's
bounds and scaled to its media duration. The highlighted interval is an estimate,
not a verified train boundary. Legacy and manually selected archive clips without
confirmed detector timing play from the beginning without an invented highlight.

### Local event analysis (development)

A separate, bounded background worker analyses completed event recordings. It
samples at most 48 detected train frames per clip; capture and recording do not
wait for analysis. Estimates are stored separately from capture evidence and
user reviews. Train-region feature motion, checked against the background,
provides screen direction. Matched rail landmarks can map that direction to a
bearing. Camera movement, inconsistent motion and ambiguous simultaneous trains
produce Unknown.

Settings → Railway calibration has an opt-in approximate speed estimate for new
recordings. It uses the two rail correspondences frozen at capture time. This
is an affine approximation affected by perspective, not a surveyed metric speed.
Missing geometry, inconsistent motion or a recording timebase mismatch produce
Unknown; present settings are never applied silently to older recordings.

Freight/passenger inference loads a separately distributed `train-type.onnx` beside
the detector model, with `train-type.json` containing `sha256`, ordered labels
`["freight", "passenger"]`, `input_size` (integer or width/height pair),
`source_sha256`, and `minimum_context_luminance`. A normalized `crop` may be
provided for a fixed-camera model. The fingerprint must match the source frozen
when the event was recorded. Weights and camera-specific manifests are distributed from the model release
bucket rather than bundled with this repository. Low model scores, disagreement, unsupported
illumination and missing artifacts produce Unknown. The existing private
camera pilot artifact has been checked for OpenCV CPU inference compatibility;
that does not establish accuracy on other cameras or at night.

When confirming a train, users can choose Freight, Passenger or Not sure.
Their label remains separate from the model estimate and can be edited later.
Dismissing a detection clears its train-type label. These development changes do
not disable cloud analysis or alter existing upload contracts. Cloud retirement
requires real-clip parity and resource measurements on the target device.

### Model versions and downloads

Settings → Models & local analysis shows installed versions, SHA-256 identities,
latest compatible releases, and download status. Select Check for updates, then
Download latest weights for the detector or type classifier. Releases come from
`https://storage.googleapis.com/trainsfyi-railwatch-models/catalog-v1.json`.
The dedicated public source bucket contains released model artifacts only.

Downloads run in the background, check size and checksum, validate ONNX inference,
and activate atomically under the writable device data directory. Failed downloads
keep the existing model. The detector switches after the current train passage
ends; local analysis uses its new classifier on the next scan. Direction and speed
use built-in algorithms, so their versions are shown without weight downloads.
Camera-scoped classifiers are unavailable for incompatible camera sources. The
current classifier is a single-camera daytime pilot, not a general classifier.
