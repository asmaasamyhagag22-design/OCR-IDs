import json
from PIL import Image
from src.geometry import cli
from src.geometry.doccornernet import Prediction


def test_outputs_rejections_and_same_stem(monkeypatch, tmp_path):
    class FakeDetector:
        def __init__(self, *args):
            self.metadata = {'repository_commit': 'test-double'}
        def predict(self, rgb):
            accepted = bool(rgb[0,0,0] > 100)
            return Prediction([[.1,.1],[.9,.1],[.9,.9],[.1,.9]], [[10,10],[90,10],[90,90],[10,90]],
                              .9 if accepted else .1, (224,224), accepted,
                              None if accepted else 'low_document_score', 1.0)
    monkeypatch.setattr(cli, 'DocCornerNet', FakeDetector)
    source, out = tmp_path/'input', tmp_path/'output'
    source.mkdir()
    Image.new('RGB', (100,100), 'white').save(source/'same.png')
    Image.new('RGB', (100,100), 'black').save(source/'same.jpg')
    (source/'broken.png').write_bytes(b'bad')
    assert cli.main(['--input',str(source),'--output',str(out)]) == 0
    records = [json.loads(line) for line in (out/'predictions.jsonl').read_text().splitlines()]
    assert len(records) == 3
    assert {r['status'] for r in records} == {'accepted','rejected','error'}
    assert len(list(out.glob('*/overlay.jpg'))) == 2
    assert len(list(out.glob('*/rectified.png'))) == 1
    assert cli.main(['--input',str(source),'--output',str(out)]) == 2
