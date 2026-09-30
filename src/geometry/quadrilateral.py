"""Validate the model's cyclic corner order before any warp."""
import numpy as np


def normalized_to_pixels(points, width, height):
    if width <= 0 or height <= 0:
        raise ValueError('Image dimensions must be positive')
    return np.asarray(points, dtype=np.float64).reshape(4, 2) * [width, height]


def cross(a, b, c):
    u, v = b - a, c - a
    return u[0] * v[1] - u[1] * v[0]


def self_intersects(points):
    p = np.asarray(points, dtype=np.float64)
    def intersects(a, b, c, d):
        return cross(a, b, c) * cross(a, b, d) <= 0 and cross(c, d, a) * cross(c, d, b) <= 0
    return bool(intersects(*p) or intersects(p[1], p[2], p[3], p[0]))


def is_convex(points):
    p = np.asarray(points, dtype=np.float64)
    turns = [cross(p[i], p[(i+1)%4], p[(i+2)%4]) for i in range(4)]
    return bool(all(t > 1e-8 for t in turns) or all(t < -1e-8 for t in turns))


def order_corners(points):
    """Order an unordered, convex set clockwise, starting at image-space TL.

    This utility must not be used to repair a self-intersecting model polygon.
    TL is the smallest x+y (ties broken by y then x). No text orientation inference.
    """
    p = np.asarray(points, dtype=np.float64).reshape(4, 2)
    if not np.isfinite(p).all():
        raise ValueError('non_finite_coordinates')
    center = p.mean(axis=0)
    p = p[np.argsort(np.arctan2(p[:, 1]-center[1], p[:, 0]-center[0]))]
    start = min(range(4), key=lambda i: (p[i].sum(), p[i, 1], p[i, 0]))
    return np.roll(p, -start, axis=0)


def validate_quadrilateral(points, width, height, tolerance=0.02, min_area_fraction=0.02):
    if width <= 0 or height <= 0 or not np.isfinite([tolerance, min_area_fraction]).all() or tolerance < 0 or not 0 < min_area_fraction <= 1:
        raise ValueError('Invalid geometry configuration')
    p = np.asarray(points, dtype=np.float64)
    if p.shape != (4, 2):
        return 'invalid_coordinate_shape'
    if not np.isfinite(p).all():
        return 'non_finite_coordinates'
    q = p / [width, height]
    if np.any(q < -tolerance) or np.any(q > 1+tolerance):
        return 'out_of_bounds'
    if len(np.unique(p, axis=0)) != 4:
        return 'degenerate_quadrilateral'
    if self_intersects(p):
        return 'self_intersection'
    if not is_convex(p):
        return 'non_convex_or_degenerate'
    area = abs(np.dot(p[:, 0], np.roll(p[:, 1], -1)) - np.dot(p[:, 1], np.roll(p[:, 0], -1))) / 2
    if area <= min_area_fraction * width * height:
        return 'area_too_small'
    if not np.allclose(p, order_corners(p), rtol=0, atol=1e-6):
        return 'invalid_corner_order'
    return None
