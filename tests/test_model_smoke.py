import json
import shutil
import numpy as np
import pytest
from src.geometry.doccornernet import DocCornerNet, DEFAULT_CHECKPOINT, ModelLoadError, strict_load_weights


@pytest.fixture(scope='module')
def detector():
    # Required integration test: missing TensorFlow/checkpoint is a failure, not a skip.
    return DocCornerNet()


def test_real_checkpoint_and_dummy(detector):
    assert detector.metadata['strict_weights_loaded']
    corners, score = detector._forward(np.zeros((1,224,224,3), dtype=np.float32))
    assert corners.shape == (4,2) and np.isfinite(corners).all()
    assert np.isfinite(score) and 0 <= score <= 1
    result = detector.predict(np.zeros((480,640,3), dtype=np.uint8))
    assert np.asarray(result.corners_original_pixels).shape == (4,2)


def test_incompatible_weights_fail_strictly(detector, tmp_path):
    import tensorflow as tf
    wrong = tf.keras.Sequential([tf.keras.Input((3,)), tf.keras.layers.Dense(2)])
    file = tmp_path/'wrong.weights.h5'
    wrong.save_weights(file)
    with pytest.raises(ValueError):
        strict_load_weights(detector.model, file)


def test_corrupted_checkpoint_fails(tmp_path):
    shutil.copy(DEFAULT_CHECKPOINT/'config.json', tmp_path/'config.json')
    (tmp_path/'best_model.weights.h5').write_bytes(b'corrupted')
    with pytest.raises(ModelLoadError) as caught:
        DocCornerNet(tmp_path)
    assert 'SHA-256 mismatch' in str(caught.value)
    assert caught.value.details['failing_file'].endswith('best_model.weights.h5')


def test_missing_checkpoint_fails(tmp_path):
    with pytest.raises(ModelLoadError) as caught:
        DocCornerNet(tmp_path)
    assert 'FileNotFoundError' in str(caught.value)
