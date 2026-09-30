# Research history: not part of the shipped service and never copied into its image
# (docker/app_code_files.txt, phase 4 final Stage 7).
"""The second model: a PaddleOCR CTC digit recognizer, reconstructed from its
bundle's inference.json and inference.yml only (no author code exists).

Declared in configs/phase4_integration_rule_v1.json (contributed_model_inference,
step_0_crop, amendment 3). The functions here are pure and run in any venv. The
Paddle predictor is created only in the worker venv (paddlepaddle 3.3.1, D-012):
the project venv's paddlepaddle 3.3.0 cannot build this PIR v4 program.

Nothing here prints a reading: readings of real cards are card-level material.

EVIDENCE ONLY (D-014): this model is not part of any deliverable. The service
ships with the bcfinal_only decider.
"""
from __future__ import annotations

import hashlib
import math
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
MODEL_DIR = ROOT / "artifacts" / "second_model" / "rec_digits_finetuned"
FILE_SHA256 = {
    "inference.json": "b315c53bb574b8229288c784b0519558b29e608f808490546316c2ca4914e2f6",
    "inference.pdiparams": "185b6080fb37b35037185b1567508bbc049a92abd2246a95124971338fa1a917",
    "inference.yml": "797da0591093b0745d0f1db98c54e74980a1e403e0d98dfc5953072543a7f33e",
}
ARABIC_INDIC = "٠١٢٣٤٥٦٧٨٩"
#: index 0 is the CTC blank; then the yml's character_dict; then the space
#: PaddleOCR appends (13 classes, matching the graph's output).
CLASSES = ("",) + ("/",) + tuple(ARABIC_INDIC) + (" ",)
REC_SHAPE = (3, 48, 320)
SLOTS = 14
_FOLD = str.maketrans(ARABIC_INDIC, "0123456789")


def verify_files(model_dir: Path = MODEL_DIR) -> dict:
    return {name: (model_dir / name).is_file()
            and hashlib.sha256((model_dir / name).read_bytes()).hexdigest() == digest
            for name, digest in FILE_SHA256.items()}


def to_bgr(rgb: np.ndarray) -> np.ndarray:
    return np.ascontiguousarray(np.asarray(rgb)[..., ::-1])


def resize_norm(bgr: np.ndarray, shape=REC_SHAPE) -> np.ndarray:
    """The framework's standard RecResizeImg: height 48 keeping the aspect ratio,
    width capped at 320, (x/255 - 0.5)/0.5, right-padded with zeros to 320."""
    import cv2

    channels, height, width = shape
    ratio = bgr.shape[1] / float(bgr.shape[0])
    resized_w = width if math.ceil(height * ratio) > width else int(math.ceil(height * ratio))
    image = cv2.resize(bgr, (resized_w, height)).astype(np.float32).transpose(2, 0, 1) / 255.0
    image = (image - 0.5) / 0.5
    out = np.zeros((channels, height, width), dtype=np.float32)
    out[:, :, :resized_w] = image
    return out[None]


def ctc_decode(prob: np.ndarray) -> dict:
    """Greedy CTC over a (T, 13) softmax: argmax per step, collapse repeats, drop
    blanks. A character's score is the maximum posterior over the steps collapsed
    into it; the reading's raw score is the minimum over its characters. Only an
    exactly-14-ASCII-digit result (after folding Arabic-Indic digits) is a
    reading; anything else is 'not 14 digits', with no reading and no score."""
    prob = np.asarray(prob)
    chars, scores, previous = [], [], -1
    for step, index in enumerate(prob.argmax(-1)):
        index = int(index)
        if index != 0 and index != previous:
            chars.append(CLASSES[index])
            scores.append(float(prob[step, index]))
        elif index != 0:
            scores[-1] = max(scores[-1], float(prob[step, index]))
        previous = index
    folded = "".join(chars).translate(_FOLD)
    is_reading = len(folded) == SLOTS and folded.isascii() and folded.isdigit()
    return {"reading": folded if is_reading else "",
            "raw_score": min(scores) if is_reading else None,
            "characters": len(chars)}


def choose_box(polygons, scores, target_xy) -> int | None:
    """The polygon whose centroid is nearest the target; ties to the higher
    detection score, then the lower index."""
    best = None
    target = np.asarray(target_xy, dtype=np.float64)
    for index, (polygon, score) in enumerate(zip(polygons, scores)):
        centroid = np.asarray(polygon, dtype=np.float64).reshape(-1, 2).mean(axis=0)
        key = (float(np.hypot(*(centroid - target))), -float(score), index)
        if best is None or key < best:
            best = key
    return None if best is None else best[2]


def min_area_crop(image: np.ndarray, polygon) -> np.ndarray:
    """PaddleOCR's minimum-area-rectangle crop and rotate-crop convention."""
    import cv2

    rect = cv2.minAreaRect(np.asarray(polygon).astype(np.int32))
    points = sorted(list(cv2.boxPoints(rect)), key=lambda p: p[0])
    a, d = (0, 1) if points[1][1] > points[0][1] else (1, 0)
    b, c = (2, 3) if points[3][1] > points[2][1] else (3, 2)
    box = np.float32([points[a], points[b], points[c], points[d]])
    width = int(max(np.linalg.norm(box[0] - box[1]), np.linalg.norm(box[2] - box[3])))
    height = int(max(np.linalg.norm(box[0] - box[3]), np.linalg.norm(box[1] - box[2])))
    target = np.float32([[0, 0], [width, 0], [width, height], [0, height]])
    crop = cv2.warpPerspective(image, cv2.getPerspectiveTransform(box, target), (width, height),
                               borderMode=cv2.BORDER_REPLICATE, flags=cv2.INTER_CUBIC)
    if crop.shape[0] * 1.0 / max(crop.shape[1], 1) >= 1.5:
        crop = np.rot90(crop)
    return np.ascontiguousarray(crop)


class SecondModel:
    """The Paddle predictor. Worker venv only (paddlepaddle 3.3.1)."""

    def __init__(self, model_dir: Path = MODEL_DIR):
        checks = verify_files(model_dir)
        if not all(checks.values()):
            raise RuntimeError(f"SECOND_MODEL_FILES_MISMATCH:{sorted(k for k, v in checks.items() if not v)}")
        import paddle.inference as inference

        config = inference.Config(str(model_dir / "inference.json"), str(model_dir / "inference.pdiparams"))
        config.disable_gpu()
        config.disable_mkldnn()
        config.disable_glog_info()
        self.predictor = inference.create_predictor(config)
        self.input = self.predictor.get_input_handle(self.predictor.get_input_names()[0])
        self.output = self.predictor.get_output_handle(self.predictor.get_output_names()[0])

    def probabilities(self, bgr: np.ndarray) -> np.ndarray:
        x = resize_norm(bgr)
        self.input.reshape(x.shape)
        self.input.copy_from_cpu(x)
        self.predictor.run()
        return self.output.copy_to_cpu()[0]

    def read(self, bgr: np.ndarray) -> dict:
        return ctc_decode(self.probabilities(bgr))
