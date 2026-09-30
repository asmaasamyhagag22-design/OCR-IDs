from itertools import permutations
import numpy as np
import pytest
from src.geometry.quadrilateral import order_corners, validate_quadrilateral, self_intersects, is_convex

SQUARE = np.array([[10, 10], [90, 10], [90, 90], [10, 90]], dtype=float)


def test_order_all_permutations():
    for points in permutations(SQUARE.tolist()):
        np.testing.assert_array_equal(order_corners(points), SQUARE)


def test_crossing_is_rejected_not_repaired():
    bow = SQUARE[[0, 2, 1, 3]]
    assert self_intersects(bow)
    assert not is_convex(bow)
    assert validate_quadrilateral(bow, 100, 100) == 'self_intersection'


@pytest.mark.parametrize('points', [np.zeros((4, 2)), [[1,1],[2,2],[3,3],[4,4]],
                                  [[10,10],[90,10],[50,40],[10,90]],
                                  [[10,10],[11,10],[11,11],[10,11]],
                                  [[-10,10],[90,10],[90,90],[10,90]],
                                  [[float('nan'),10],[90,10],[90,90],[10,90]],
                                  SQUARE[::-1]])
def test_reject_invalid(points):
    assert validate_quadrilateral(points, 100, 100) is not None


def test_valid_and_tolerance():
    assert is_convex(SQUARE)
    assert not self_intersects(SQUARE)
    assert validate_quadrilateral(SQUARE, 100, 100) is None
    p = np.array([[-1,0],[100,0],[100,100],[-1,100]])
    assert validate_quadrilateral(p, 100, 100, tolerance=0.02) is None
    assert validate_quadrilateral(p, 100, 100, tolerance=0) == 'out_of_bounds'
