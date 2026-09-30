import cv2
import numpy as np
import pytest
from src.geometry.rectify import rectify


def test_known_perspective_grid():
    grid = np.full((126,200,3), 240, dtype=np.uint8)
    grid[::21] = 0
    grid[:,::25] = 0
    # Asymmetric colored markers detect accidental rotation or mirroring.
    grid[10:30,10:30] = [255,0,0]
    grid[90:110,160:180] = [0,0,255]
    target = np.float32([[0,0],[199,0],[199,125],[0,125]])
    corners = np.float32([[40,35],[260,20],[280,195],[25,210]])
    scene = cv2.warpPerspective(grid, cv2.getPerspectiveTransform(target, corners), (320,240))
    result = rectify(scene, corners, 200,126)
    assert result.shape == grid.shape
    assert np.mean(np.abs(result.astype(float)-grid.astype(float))) < 12
    np.testing.assert_allclose(result[20,20], [255,0,0], atol=2)
    np.testing.assert_allclose(result[100,170], [0,0,255], atol=2)


def test_invalid_cannot_warp():
    with pytest.raises(ValueError):
        rectify(np.zeros((100,100,3), dtype=np.uint8), np.zeros((4,2)))
