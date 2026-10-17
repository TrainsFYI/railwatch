# Railwatcher

A Raspberry Pi lookout for trains.fyi. Connect a USB webcam or local RTSP camera, place it on a satellite map, and record passing trains with a local train detector.

## What works

- Local browser wizard: camera selection, live preview, satellite position, railway overlay, draggable pin, bearing and field-of-view cone.
- Camera preview, settings, history and clips available to any device on the local network.
- One FFmpeg camera connection, independent local inference, repeated-positive confirmation, pre/post-roll MP4 recordings and a low-bitrate 24-hour rolling footage archive.
- Day-and-night inference with the existing TrainsFYI single-class YOLOv8 model, exported to ONNX and run using OpenCV DNN. PyTorch is not needed on the Pi.
- SQLite sightings history, local video playback, persisted upload retries and bounded storage. Uploaded clips are pruned oldest first; pending clips are never automatically discarded. Capture pauses when pending clips consume the storage budget.
- systemd installer, service restart recovery, camera reconnection, and local dashboard with real device/queue status.

**Account-linked camera pilot:** choose Connect to TrainsFYI in setup or Settings, then approve the camera through your TrainsFYI account in a browser. The pilot is invite-only. Unpaired cameras record locally. Confirmed passages upload privately through the [device protocol](docs/device-protocol.md); cloud processing and models remain in the private backend.

## Install on a Raspberry Pi

### Recommended hardware

For one camera, start with:

- **Raspberry Pi 5, 4 GB** (8 GB if you also plan to run other services). This is a suggested baseline; inference speed and temperature still need checking with your camera/model. Use the official 27 W USB-C power supply and an active cooler or fan case for continuous inference. Railwatcher supports 64-bit Raspberry Pi OS; the Pi 5 has a 64-bit quad-core CPU and USB 3 for camera/storage peripherals. See the [Pi 5 specifications](https://www.raspberrypi.com/products/raspberry-pi-5/).
- **Wired Ethernet for the Pi** where possible, especially when the camera streams over Wi-Fi. Use a good quality 64 GB or larger microSD card for the OS; an external USB 3 SSD is preferred for video storage and sustained use.
- **One camera with a stable 1280×720 or higher stream.** A USB UVC webcam can connect directly to the Pi. For an outdoor trackside view, choose a weather-rated camera with night/IR support and a local RTSP stream; keep the camera's power and network connection stable.

**Recommended budget camera: Wyze Cam v3.** Current production firmware includes RTSP support; Wyze announced the production rollout on February 2, 2026, with the RTSP setting available in the Wyze app ([firmware announcement](https://forums.wyze.com/t/wyze-firmware-updates-2-2-2026/340669)). Update the Wyze app to version 3.9 or later and the camera firmware, then enable RTSP in the camera's advanced settings, set its RTSP username and password, and paste the generated stream URL into Railwatcher. Wyze rolls firmware out gradually, so update the app and camera before installation if the RTSP setting is missing. The v3 has night vision and needs its own USB power supply; it does not provide PoE ([Wyze v3 FAQ](https://support.wyze.com/hc/en-us/articles/7816811330331-More-Wyze-Cam-v3-FAQs)).

### Install

1. In [Raspberry Pi Imager](https://www.raspberrypi.com/software/), install **Raspberry Pi OS Lite (64-bit)** on the Pi's microSD card. In Imager's settings, set a hostname (for example `railwatcher`), create a user and password, enable SSH, and enter the Wi-Fi details if Ethernet is unavailable. Raspberry Pi's [OS install guide](https://www.raspberrypi.com/documentation/computers/getting-started.html) has screenshots and details. Boot the Pi and connect over SSH or use a keyboard and display.
2. Put a copy of the Railwatcher project on the Pi. From a computer that already has this project checked out, run the following from the project directory (replace `pi` if you chose another username):

   ```sh
   rsync -av --exclude='.runtime' --exclude='.venv' --exclude='data' --exclude='artifacts' --exclude='__pycache__' ./ pi@railwatcher.local:~/railwatcher/
   ```

   If the Pi hostname does not resolve, use its IP address instead of `railwatcher.local`. Alternatively, clone your Railwatcher repository directly on the Pi.
3. If you have the train detector model, place it at `models/train.onnx` in the project directory before installing. The model is not included in source control; see [Model export](#model-export). If you export it on your computer after copying the project, transfer it with `scp models/train.onnx models/train.json pi@railwatcher.local:~/railwatcher/models/` (replace the username/host as needed). Without the model, the web UI and camera preview still work, but train detection stays unavailable.
4. Connect the USB camera, or configure the network camera's RTSP stream and confirm it is reachable from the Pi. On the Pi, run:

   ```sh
   cd ~/railwatcher
   sudo bash scripts/install.sh
   ```

   The installer adds FFmpeg and Python dependencies, creates the `railwatcher` system service, and prints the local URL. It copies `models/train.onnx` and its accompanying `train.json` if present. Installation needs an internet connection to download system and Python packages.
5. From a phone or computer on the same home network, open `http://railwatcher.local:8787` (or the URL printed by the installer). On first visit, choose the camera, set its position and direction on the map, and complete setup. The live dashboard opens on later visits; use **Settings** to change the camera or other configuration.

The page is available to anyone on the local network; no login is required. Use it on a trusted network and do not port-forward the service to the public internet. Camera connection credentials, device credentials and pairing secrets are withheld from the configuration response and stored in the restricted data directory.

The installer installs OS/Python dependencies, a dedicated `railwatcher` service user in the video group, and a boot-enabled systemd service. It can be rerun to update the application without replacing settings/history. No cloud services are deployed.

```sh
sudo systemctl status railwatcher
sudo journalctl -u railwatcher -f
```


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

The detector keeps 12 seconds of recent frames in RAM for event pre-roll, confirms three positive samples within five seconds, and records until 15 seconds after the last positive. Preview, inference and event clips use 960×540 at 10 fps; inference samples the latest frame independently, at most twice per second. In parallel, the camera feed is encoded at 640×360 and 5 fps into two-minute H.264 files, retained for up to 24 hours. The 350 kbit/s bitrate ceiling uses about 3.8 GB per day at the maximum rate. The **Review footage** page lets you play retained segments and select up to 15 minutes to save as a manually marked train sighting; it joins the normal history and upload queue. The current segment is finalized at each segment boundary, so the newest footage can take up to two minutes to appear. A gap in camera capture is not filled with fabricated footage.

Recordings longer than five minutes are split into clips with overlapping pre-roll. A stationary train can keep the detector active; this version detects train presence rather than estimating movement or counting individual trains. Dashboard totals count recording events, including interrupted attempts, not unique physical trains. Restart/disconnection closes the current recording where possible; footage while disconnected cannot be recovered.

The detector runs at night, but the installed model has not been validated here on nighttime/IR footage. Camera illumination, IR reflections, passing cars and rain need field testing and likely more training data. There is no promise of complete recall, train identity or direction-of-travel inference. Camera bearing describes where the lens faces, not where the train is headed.

Storage is a recording budget, with a conservative reserve for the next maximum-length clip and 200 MB of free-disk headroom. The rolling archive removes footage older than 24 hours and sheds its oldest segments if free disk space drops below 1 GB; the dashboard reports the footage currently available. A 64 GB microSD card is the practical minimum for the OS, event clips and rolling archive; a USB 3 SSD is recommended for sustained camera recording. Metadata is retained after uploaded videos are pruned. Queued clips remain local until accepted. There is no automatic deletion of unsubmitted sightings. Pausing detection also pauses camera capture and the rolling archive. Audio is never captured.

The wizard bundles Leaflet locally; satellite imagery and highlighted railways are online services. Imagery is supplied by Esri; rail tiles/data by OpenRailwayMap/OpenStreetMap, with attribution shown on the map. The default public rail tiles are for this development setup. Before public/commercial distribution, provide a permitted or self-hosted rail tile endpoint according to [OpenRailwayMap's usage terms](https://wiki.openstreetmap.org/wiki/OpenRailwayMap/API); default endpoints are in `railwatcher/static/app.js`. Manual coordinate entry continues to work without tiles. Browser geolocation generally needs HTTPS except on localhost.

## Movement review

**Review footage → Significant movement in tracked area** filters completed archive segments using the camera’s saved detection polygon (or the full frame if no polygon is set). A background worker indexes existing recordings oldest first and then new segments. Results persist across restarts, expire with the rolling archive, and are reanalysed when the camera source or polygon changes. The page shows analysed/pending/unavailable counts and refreshes automatically. **All footage** remains available while indexing runs. Select a segment and use **Jump to movement** to seek to candidate windows; the save-clip range is prefilled around the selected window. Saving remains an explicit manual train marking and uses the existing upload queue.

The motion heuristic samples twice per second at 320×180, compensates uniform exposure changes, and requires at least 15% of pixels within the polygon to change over four consecutive samples spanning at least 1.8 seconds. This rejects minor swaying and brief flashes; strong wind can still trigger it. Vehicles, people, foliage, camera movement and shadows can trigger it. Small, slow or brief movement can be missed. These are movement candidates, not train detections or validated train boundaries. No movement detected is not proof of a clear track. Analysis failures are shown separately from quiet footage. Indexing does not create sightings or upload clips automatically.

## Highlightable landmarks

In Camera settings, choose **Add landmark**, enter a label, and drag a box on the live image. Select a saved landmark to rename it or draw a replacement box. Remove unwanted landmarks and use **Save settings** to persist changes. Boxes use image-relative coordinates and appear on the live dashboard. Up to 32 landmarks can be saved. Landmarks annotate fixed features and define the regions used by the red-flash review filter.

## Crossing-light review

**Red flashes in landmarks** remains available for reviewing cached results, but its background indexing is paused. **Train model detections** is the default review filter. It examines the original archive frames, requiring at least three red pulses with intervening dark intervals and similar cadence. Jump buttons show the landmark label. **Significant movement** remains a separate option. Red-flash results persist, reanalyse when landmarks change, and index newest recordings first. These are crossing-activity candidates, not confirmed train detections. Tiny/distant lights, compression, occlusion, unusual flash cadence, or white/saturated lamps can cause missed events; other flashing red objects within a selected box can cause false matches. A real flashing example is needed to validate sensitivity for each camera.

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
