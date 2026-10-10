#!/usr/bin/env python3
"""Export a trusted YOLOv8 checkpoint with a named train class for OpenCV DNN."""

import argparse
import hashlib
import json
import shutil
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("checkpoint", type=Path)
    parser.add_argument("--output", type=Path, default=Path("models/train.onnx"))
    args = parser.parse_args()
    if not args.checkpoint.is_file():
        parser.error("Checkpoint does not exist")
    import cv2
    import numpy as np
    from ultralytics import YOLO

    # Export beside a local copy, never modify the source TrainsFYI checkout.
    args.output.parent.mkdir(parents=True, exist_ok=True)
    local = args.output.parent / "export-source.pt"
    shutil.copy2(args.checkpoint, local)
    model = YOLO(str(local))
    train_classes = [key for key, name in model.names.items() if name == "train"]
    if len(train_classes) != 1:
        raise ValueError("Expected exactly one named train class")
    result = Path(model.export(format="onnx", imgsz=640, opset=12, simplify=False, dynamic=False, nms=False))
    net = cv2.dnn.readNetFromONNX(str(result))
    net.setInput(np.zeros((1, 3, 640, 640), dtype=np.float32))
    output = net.forward()
    if output.shape != (1, 4 + len(model.names), 8400):
        raise ValueError(f"Unexpected exported output shape: {output.shape}")
    shutil.copy2(result, args.output)
    manifest = {
        "architecture": args.checkpoint.stem,
        "classes": model.names,
        "train_class_id": train_classes[0],
        "focus_on_area": True,
        "input_size": 640,
        "source_sha256": hashlib.sha256(args.checkpoint.read_bytes()).hexdigest(),
        "onnx_sha256": hashlib.sha256(args.output.read_bytes()).hexdigest(),
    }
    args.output.with_suffix(".json").write_text(json.dumps(manifest, indent=2) + "\n")
    local.unlink()
    result.unlink()
    print(f"Verified model written to {args.output}")


if __name__ == "__main__":
    main()
