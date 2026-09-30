import numpy as np
from PIL import Image
from src.geometry.quadrilateral import normalized_to_pixels
from src.geometry.doccornernet import preprocess
from src.geometry.rectify import load_rgb


def test_non_square_coordinates():
    np.testing.assert_allclose(normalized_to_pixels([[0,0],[1,0],[1,1],[0,1]], 1200, 800),
                               [[0,0],[1200,0],[1200,800],[0,800]])
    np.testing.assert_allclose(normalized_to_pixels([[.1,.2]]*4, 1200, 800), [[120,160]]*4)


def test_preprocessing_matches_training():
    rgb = np.random.default_rng(1).integers(0, 256, (90, 210, 3), dtype=np.uint8)
    resized = np.asarray(Image.fromarray(rgb).resize((224,224), Image.Resampling.BILINEAR), dtype=np.float32)
    expected = (resized/255 - np.float32([.485,.456,.406]))/np.float32([.229,.224,.225])
    actual = preprocess(rgb)
    assert actual.dtype == np.float32 and actual.shape == (1,224,224,3)
    np.testing.assert_array_equal(actual[0], expected)


def test_exif_orientation_and_rgb(tmp_path):
    image = Image.new('RGB', (80,40), 'red')
    exif = image.getexif()
    exif[274] = 6
    path = tmp_path/'rotated.jpg'
    image.save(path, exif=exif)
    assert load_rgb(path).shape == (80,40,3)
    gray = tmp_path/'gray.png'
    Image.new('L', (20,30), 100).save(gray)
    assert load_rgb(gray).shape == (30,20,3)
