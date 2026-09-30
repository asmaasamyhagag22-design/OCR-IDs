"""RGB image loading, overlays, and validated OpenCV perspective transforms."""
import cv2
import numpy as np
from PIL import Image, ImageOps, ImageDraw
from .quadrilateral import validate_quadrilateral
from ..governance import guard


def load_rgb(path):
    # Every internal card image in this repository is decoded here. The
    # PreToolUse hook cannot see this call, so the guard is in the loader.
    guard.check_card_read(path)
    with Image.open(path) as image:
        return np.array(ImageOps.exif_transpose(image).convert('RGB'))


def overlay(rgb, points, label):
    image = Image.fromarray(rgb)
    draw = ImageDraw.Draw(image)
    p = np.asarray(points)
    if p.shape == (4, 2) and np.isfinite(p).all():
        # Limit visualization coordinates; never alter prediction/warp coordinates.
        p = np.clip(p, -1e6, 1e6).tolist()
        draw.line([tuple(v) for v in p + p[:1]], fill='red', width=4)
        for name, point in zip(('TL', 'TR', 'BR', 'BL'), p):
            draw.text(tuple(point), name, fill='yellow')
    draw.text((8, 8), label, fill='red', stroke_width=1, stroke_fill='white')
    return image


def rectify(rgb, points, width=1000, height=630, tolerance=0.02, min_area_fraction=0.02):
    if width < 2 or height < 2:
        raise ValueError('Output dimensions must be at least 2')
    reason = validate_quadrilateral(points, rgb.shape[1], rgb.shape[0], tolerance, min_area_fraction)
    if reason:
        raise ValueError(reason)
    target = np.float32([[0, 0], [width-1, 0], [width-1, height-1], [0, height-1]])
    matrix = cv2.getPerspectiveTransform(np.float32(points), target)
    return cv2.warpPerspective(rgb, matrix, (width, height), flags=cv2.INTER_LINEAR)
