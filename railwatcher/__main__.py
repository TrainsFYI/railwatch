import argparse
import logging
import os
from pathlib import Path

import uvicorn

from .app import create_app


def main():
    parser = argparse.ArgumentParser(description="RailWatch Raspberry Pi camera client")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8787)
    parser.add_argument("--data", type=Path, default=Path(os.environ.get("RAILWATCHER_DATA", "data")))
    parser.add_argument(
        "--model", type=Path, default=Path(os.environ.get("RAILWATCHER_MODEL", "models/train.onnx"))
    )
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO)
    app = create_app(args.data, args.model)
    print(f"Open http://127.0.0.1:{args.port} or http://<device-name>.local:{args.port}", flush=True)
    uvicorn.run(app, host=args.host, port=args.port, access_log=False)


if __name__ == "__main__":
    main()
