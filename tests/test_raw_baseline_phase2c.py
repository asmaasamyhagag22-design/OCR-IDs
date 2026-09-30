from pathlib import Path

import numpy as np
import pytest

from src.extraction.ocr import (OFFICIAL_PADDLE_MODEL_SOURCES, RawOcrResult,
                                TesseractEngine, TesseractPsmSweepEngine)
from src.extraction.regions import load_region_config, region_by_role


def test_v5_preserves_v4_geometry_and_only_reroutes_the_serial():
    """v5's single field change remains the serial's recognition model.

    The birth-date candidates added in Phase 2D-B are geometry, so they must
    appear identically in both configs; v5 is not allowed to drift from v4 on
    anything but the serial's language.
    """
    v4 = load_region_config(Path('configs/field_regions_v4.json'))
    v5 = load_region_config(Path('configs/field_regions_v5.json'))
    for side in ('front', 'back'):
        v4_boxes = {region.name: region.bbox for region in v4['layouts'][side]}
        v5_boxes = {region.name: region.bbox for region in v5['layouts'][side]}
        assert v5_boxes == v4_boxes
        # Whatever birth-date candidates exist, both configs declare the same
        # ones and no unconfirmed band is among them.
        v4_dates = [r.name for r in v4['layouts'][side] if r.role == 'printed_birth_date']
        v5_dates = [r.name for r in v5['layouts'][side] if r.role == 'printed_birth_date']
        assert v4_dates == v5_dates
        for region in v5['layouts'][side]:
            if region.role == 'printed_birth_date':
                assert region.semantics == 'visually_confirmed', region.name
    assert region_by_role(v5['layouts']['back'], 'printed_birth_date') is None
    serial = next(region for region in v5['layouts']['front']
                  if region.name == 'card_serial_number')
    assert serial.content == 'latin_serial'


def test_raw_tesseract_preprocess_is_identity():
    engine = TesseractEngine.__new__(TesseractEngine)
    image = np.arange(3 * 5 * 7, dtype=np.uint8).reshape((5, 7, 3))
    result = engine.preprocess(image)
    np.testing.assert_array_equal(result, image)
    assert result.shape == image.shape


def test_raw_tesseract_rejects_enhancement_settings(monkeypatch):
    fake = type('FakePytesseract', (), {
        'pytesseract': type('Config', (), {'tesseract_cmd': None})(),
        'get_tesseract_version': staticmethod(lambda: 'test'),
    })
    monkeypatch.setitem(__import__('sys').modules, 'pytesseract', fake)
    with pytest.raises(ValueError, match='forbids OCR preprocessing'):
        TesseractEngine(upscale=3.0)


def test_psm_sweep_preserves_every_attempt_and_selected_raw_text():
    class Base:
        version = 'test'
        tessdata_dir = None

        def recognize(self, image, charset='', lang='eng', psm=7, region_name=''):
            return RawOcrResult(text=f'raw-{psm}\n', confidence=psm / 100,
                                engine='tesseract', engine_version='test',
                                params={'psm': psm, 'input_crop_shape': [10, 10, 3],
                                        'latency_ms_after_warmup': float(psm)})

    engine = TesseractPsmSweepEngine.__new__(TesseractPsmSweepEngine)
    engine.base = Base()
    engine.psm_candidates = (6, 7, 8, 11, 13)
    engine.version = 'test'
    engine.tessdata_dir = None
    result = engine.recognize(np.zeros((10, 10, 3), dtype=np.uint8),
                              region_name='name_line_1')
    assert result.text == 'raw-13\n'
    assert [attempt['psm'] for attempt in result.params['attempts']] == [6, 7, 8, 11, 13]
    assert result.params['selection_without_ground_truth'] is True
    assert result.params['input_crop_shape'] == [10, 10, 3]
    assert 'latency_ms_after_warmup' in result.params


def test_official_arabic_v5_model_source_is_explicit():
    source = OFFICIAL_PADDLE_MODEL_SOURCES['arabic_PP-OCRv5_mobile_rec']
    assert source.startswith('https://paddle-model-ecology.bj.bcebos.com/')
    assert source.endswith('/arabic_PP-OCRv5_mobile_rec_infer.tar')
