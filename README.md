# Railwatcher

A Raspberry Pi lookout for trains.fyi. Connect a USB webcam or local RTSP camera, place it on a satellite map, and record passing trains with a local train detector.

## What works

- Local browser wizard: camera selection, live preview, satellite position, railway overlay, draggable pin, bearing and field-of-view cone.
- Camera preview, settings, history and clips available to any device on the local network.
- One FFmpeg camera connection, bounded in-memory JPEG buffer, independent local inference, repeated-positive confirmation and pre/post-roll MP4 recordings.
- Day-and-night inference with the existing TrainsFYI single-class YOLOv8 model, exported to ONNX and run using OpenCV DNN. PyTorch is not needed on the Pi.
- SQLite sightings history, local video playback, persisted upload retries and bounded storage. Uploaded clips are pruned oldest first; pending clips are never automatically discarded. Capture pauses when pending clips consume the storage budget.
- systemd installer, service restart recovery, camera reconnection, and local dashboard with real device/queue status.

**The TrainsFYI camera API does not exist yet.** Leave the endpoint empty to record locally. API keys can be saved now but cannot yet be verified against TrainsFYI. The upload adapter is implemented against the explicit [proposed contract](docs/upload-contract.md), not an invented production endpoint.

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
3. If you have the train detector model, place it at `models/train.onnx` in the project directory before installing. The model is not included in source control; see [Model export](#model-export). If you export it on your computer after copying the project, transfer it with `scp models/train.onnx pi@railwatcher.local:~/railwatcher/models/train.onnx` (replace the username/host as needed). Without the model, the web UI and camera preview still work, but train detection stays unavailable.
4. Connect the USB camera, or configure the network camera's RTSP stream and confirm it is reachable from the Pi. On the Pi, run:

   ```sh
   cd ~/railwatcher
   sudo bash scripts/install.sh
   ```

   The installer adds FFmpeg and Python dependencies, creates the `railwatcher` system service, and prints the local URL. It copies `models/train.onnx` if present. Installation needs an internet connection to download system and Python packages.
5. From a phone or computer on the same home network, open `http://railwatcher.local:8787` (or the URL printed by the installer). On first visit, choose the camera, set its position and direction on the map, and complete setup. The live dashboard opens on later visits; use **Settings** to change the camera or other configuration.

The page is available to anyone on the local network; no login is required. Use it on a trusted network and do not port-forward the service to the public internet. Camera credentials and API keys are withheld from the configuration response and stored in the restricted data directory.

The installer installs OS/Python dependencies, a dedicated `railwatcher` service user in the video group, and a boot-enabled systemd service. It can be rerun to update the application without replacing settings/history. No cloud services are deployed.

```sh
sudo systemctl status railwatcher
sudo journalctl -u railwatcher -f
```


## Model export

Export on a development machine, then copy the resulting ONNX file to the Pi. Only load a trusted checkpoint: PyTorch checkpoints can contain executable code.

```sh
python3.12 -m venv .venv
.venv/bin/pip install -e '.[dev,export]'
.venv/bin/python scripts/export_model.py \
  /path/to/yolov8n-trains.pt
```

This uses the TrainsFYI **single train class (class 0)** checkpoint, exports a fixed 640×640 YOLOv8 graph without NMS, validates the output with OpenCV, and writes SHA-256 provenance to `models/train.json`. The source checkout is not modified. Model binaries are ignored by git. Deployment only needs the ONNX file; training/export packages stay on the development machine. An already-installed Pi expects `/opt/railwatcher/models/train.onnx`. Missing or incompatible models are visible on the dashboard, not silently replaced with a motion detector.

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

The default buffer saves 12 seconds before the first positive inference, confirms three consecutive positive samples, and records until 15 seconds after the last positive. Preview and recording are 960×540 at 10 fps; inference samples the latest frame independently, at most twice per second. The buffer is RAM-only and bounded to 45 seconds. Very slow inference can exhaust the pre-roll history; test with your actual Pi/camera.

Recordings longer than five minutes are split into clips with overlapping pre-roll. A stationary train can keep the detector active; this version detects train presence rather than estimating movement or counting individual trains. Dashboard totals count recording events, including interrupted attempts, not unique physical trains. Restart/disconnection closes the current recording where possible; footage while disconnected cannot be recovered.

The detector runs at night, but the current TrainsFYI checkpoint has not been validated here on nighttime/IR footage. Camera illumination, IR reflections, passing cars and rain need field testing and likely more training data. There is no promise of complete recall, train identity or direction-of-travel inference. Camera bearing describes where the lens faces, not where the train is headed.

Storage is a recording budget, with a conservative reserve for the next maximum-length clip and 200 MB of free-disk headroom. Metadata is retained after uploaded videos are pruned. Queued clips remain local until accepted. There is no automatic deletion of unsubmitted sightings. Preview remains available while detection is paused. Audio is never captured.

The wizard bundles Leaflet locally; satellite imagery and highlighted railways are online services. Imagery is supplied by Esri; rail tiles/data by OpenRailwayMap/OpenStreetMap, with attribution shown on the map. The default public rail tiles are for this development setup. Before public/commercial distribution, provide a permitted or self-hosted rail tile endpoint according to [OpenRailwayMap's usage terms](https://wiki.openstreetmap.org/wiki/OpenRailwayMap/API); default endpoints are in `railwatcher/static/app.js`. Manual coordinate entry continues to work without tiles. Browser geolocation generally needs HTTPS except on localhost.
