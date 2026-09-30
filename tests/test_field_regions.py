"""The versioned field region config and its debug overlay."""
import json

import numpy as np
import pytest

from src.extraction.regions import (DEFAULT_REGION_CONFIG, PRIOR_REGION_CONFIG,
                                    V2_REGION_CONFIG, V3_REGION_CONFIG,
                                    V4_REGION_CONFIG, V5_REGION_CONFIG,
                                    RegionConfigError,
                                    debug_overlay, load_policy,
                                    load_region_config, region_by_role,
                                    regions_by_role)

CANONICAL = (1000, 630)


def test_shipped_config_loads_and_declares_both_layouts():
    config = load_region_config()
    assert config['config_version'] == 'field_regions_v4'
    assert config['canonical_size'] == CANONICAL
    assert set(config['layouts']) == {'front', 'back'}


def test_the_superseded_prior_config_still_loads():
    """Versioning is real: v1 stays loadable after v2 becomes the default."""
    prior = load_region_config(PRIOR_REGION_CONFIG)
    assert prior['config_version'] == 'field_regions_v1'
    assert prior['calibration_status'] == 'uncalibrated_prior'
    assert set(prior['layouts']) == {'front', 'back'}


def test_phase2a_keeps_v2_loadable_as_the_audited_input():
    v2 = load_region_config(V2_REGION_CONFIG)
    assert v2['config_version'] == 'field_regions_v2'
    assert v2['calibration_status'] == 'fitted_geometry_unverified_semantics'


def test_phase2a_v3_semantic_correction_remains_loadable():
    v3 = load_region_config(V3_REGION_CONFIG)
    assert v3['config_version'] == 'field_regions_v3'
    assert v3['calibration_status'] == 'phase2a_layout_audited_semantics_incomplete'


def test_regions_are_relative_and_resolve_inside_the_canvas():
    config = load_region_config()
    for layout, regions in config['layouts'].items():
        assert regions, f'{layout} declares no regions'
        for region in regions:
            x, y, w, h = region.rect
            assert 0 <= x and 0 <= y and x + w <= 1.0 and y + h <= 1.0
            x0, y0, x1, y1 = region.bbox
            assert 0 <= x0 < x1 <= CANONICAL[0]
            assert 0 <= y0 < y1 <= CANONICAL[1]


def test_bbox_arithmetic_matches_the_documented_formula():
    config = load_region_config()
    for regions in config['layouts'].values():
        for region in regions:
            x, y, w, h = region.rect
            assert region.bbox == (round(x * 1000), round(y * 630),
                                   round((x + w) * 1000), round((y + h) * 630))


def test_the_printed_birth_date_is_claimed_only_where_it_was_audited():
    """The front print is real and declared; the back has none.

    Phase 2D-B audited the YEAR/MONTH print beside the eagle emblem on all
    five accepted front crops and added it. The safety rule that produced the
    old "no printed birth date anywhere" assertion still holds in its real
    form: a birth-date region may only exist where an audit confirmed it, and
    an unconfirmed geometric band must never be offered to the check.
    """
    config = load_region_config()
    for layout, regions in config['layouts'].items():
        assert region_by_role(regions, 'id_number') is not None, layout
    assert region_by_role(config['layouts']['back'], 'printed_birth_date') is None
    assert not [r for r in config['layouts']['back'] if 'birth_date' in r.name]

    candidates = regions_by_role(config['layouts']['front'], 'printed_birth_date')
    assert [r.name for r in candidates] == ['birth_date_printed_a', 'birth_date_printed_b']
    for region in candidates:
        assert region.semantics == 'visually_confirmed', region.name
        assert region.content == 'date_digits', region.name
        assert region.printed_precision == 'year_month', region.name
        assert region.readable, region.name


def test_every_declared_birth_date_candidate_was_visually_confirmed():
    """No unconfirmed band may reach the birth-date cross-check."""
    for path in (V4_REGION_CONFIG, V5_REGION_CONFIG):
        config = load_region_config(path)
        for regions in config['layouts'].values():
            for region in regions:
                if region.role == 'printed_birth_date':
                    assert region.semantics == 'visually_confirmed', (path, region.name)


def test_the_birth_date_candidates_never_overlap_another_field():
    """The print sits in its own space, clear of the serial and the ID."""
    config = load_region_config()
    front = config['layouts']['front']
    candidates = regions_by_role(front, 'printed_birth_date')
    others = [r for r in front if r.role != 'printed_birth_date']
    for candidate in candidates:
        cx0, cy0, cx1, cy1 = candidate.bbox
        for other in others:
            ox0, oy0, ox1, oy1 = other.bbox
            overlap = (min(cx1, ox1) - max(cx0, ox0)) > 0 and (min(cy1, oy1) - max(cy0, oy0)) > 0
            assert not overlap, f'{candidate.name} overlaps {other.name}'


def test_id_number_region_is_numeric_and_fourteen_digits():
    config = load_region_config()
    for regions in config['layouts'].values():
        region = region_by_role(regions, 'id_number')
        assert region.content == 'digits'
        assert region.expected_length == 14


def test_name_regions_are_arabic_only():
    config = load_region_config()
    for region in config['layouts']['front']:
        if region.role in ('name', 'address'):
            assert region.content == 'arabic_text'


def test_config_declares_its_calibration_status_honestly():
    config = load_region_config()
    assert config['calibration_status'] == 'phase2b_visually_audited_canonical_upright'


def test_each_layout_keeps_exactly_one_id_number_region():
    config = load_region_config()
    for regions in config['layouts'].values():
        assert len([r for r in regions if r.role == 'id_number']) == 1


def test_phase2a_audited_non_date_landmarks_remain_non_readable():
    config = load_region_config(V3_REGION_CONFIG)
    audited = [
        region
        for regions in config['layouts'].values()
        for region in regions
        if region.semantics == 'audited_not_birth_date'
    ]
    assert len(audited) == 3
    assert all(region.role == 'none' and not region.readable for region in audited)


def test_v4_front_contains_only_the_requested_real_targets():
    config = load_region_config()
    assert {region.name for region in config['layouts']['front']} == {
        'portrait',
        'name_line_1',
        'name_line_2',
        'address_line_1',
        'address_line_2',
        # Two ordered candidate boxes over one audited print (Phase 2D-B).
        'birth_date_printed_a',
        'birth_date_printed_b',
        'national_id_number',
        'card_serial_number',
    }


def test_v4_back_keeps_barcode_separate_and_omits_unconfirmed_issue_date():
    config = load_region_config()
    back = {region.name: region for region in config['layouts']['back']}
    assert back['barcode_area'].content == 'none'
    assert back['barcode_area'].readable is False
    assert 'issue_date' not in back
    assert 'expiry_date' in back


def test_v4_config_is_in_the_versioned_configs_directory():
    assert DEFAULT_REGION_CONFIG.parent.name == 'configs'
    assert DEFAULT_REGION_CONFIG.name == 'field_regions_v4.json'


def test_region_crop_returns_the_declared_pixels():
    config = load_region_config()
    image = np.zeros((630, 1000, 3), dtype=np.uint8)
    region = region_by_role(config['layouts']['front'], 'id_number')
    x0, y0, x1, y1 = region.bbox
    image[y0:y1, x0:x1] = 200
    crop = region.crop(image)
    assert crop.shape == (y1 - y0, x1 - x0, 3)
    assert (crop == 200).all()


def test_overlay_draws_every_region():
    config = load_region_config()
    image = np.full((630, 1000, 3), 255, dtype=np.uint8)
    regions = config['layouts']['front']
    overlay = np.asarray(debug_overlay(image, regions, title='front'))
    assert overlay.shape == (630, 1000, 3)
    # Boxes and labels must actually mark the canvas.
    assert (overlay != 255).any()


def test_policy_config_is_versioned_and_conservative():
    policy = load_policy()
    assert policy['config_version'] == 'extraction_policy_v1'
    assert 0 < policy['confidence_threshold'] <= 1
    assert policy['repaired_field_state'] == 'needs_review'
    assert policy['max_repair_edits'] == 2


@pytest.mark.parametrize('mutation,message', [
    ({'canonical_size': [1, 1]}, 'canonical_size'),
    ({'layouts': {}}, 'no layouts'),
])
def test_malformed_config_is_rejected(tmp_path, mutation, message):
    data = json.loads(DEFAULT_REGION_CONFIG.read_text(encoding='utf-8'))
    data.update(mutation)
    path = tmp_path / 'broken.json'
    path.write_text(json.dumps(data), encoding='utf-8')
    with pytest.raises(RegionConfigError) as excinfo:
        load_region_config(path)
    assert message in str(excinfo.value)


def test_rect_escaping_the_unit_square_is_rejected(tmp_path):
    data = json.loads(DEFAULT_REGION_CONFIG.read_text(encoding='utf-8'))
    data['layouts']['front']['regions'][0]['rect'] = [0.9, 0.9, 0.5, 0.5]
    path = tmp_path / 'escaping.json'
    path.write_text(json.dumps(data), encoding='utf-8')
    with pytest.raises(RegionConfigError, match='escapes the unit square'):
        load_region_config(path)


def test_duplicate_region_names_are_rejected(tmp_path):
    data = json.loads(DEFAULT_REGION_CONFIG.read_text(encoding='utf-8'))
    regions = data['layouts']['front']['regions']
    regions.append(dict(regions[0]))
    path = tmp_path / 'duplicate.json'
    path.write_text(json.dumps(data), encoding='utf-8')
    with pytest.raises(RegionConfigError, match='duplicate region name'):
        load_region_config(path)


def test_two_id_number_regions_are_rejected(tmp_path):
    data = json.loads(DEFAULT_REGION_CONFIG.read_text(encoding='utf-8'))
    regions = data['layouts']['front']['regions']
    clone = dict(next(r for r in regions if r.get('role') == 'id_number'))
    clone['name'] = 'national_id_number_again'
    regions.append(clone)
    path = tmp_path / 'two_ids.json'
    path.write_text(json.dumps(data), encoding='utf-8')
    with pytest.raises(RegionConfigError, match='more than one id_number'):
        load_region_config(path)
