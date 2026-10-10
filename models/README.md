# Model files

Every model file the service image needs is committed in this repository. A
fresh clone builds the image with no download beyond the pinned Python base
image and the pinned Python packages.

The files stay at the paths the code and the Dockerfile load them from. Moving
them would change the image, so this folder holds the index, the hashes and the
licence notices instead of the files themselves.

- `MODELS.sha256`: the 13 model files, with paths relative to the repository root.
  It has the same hashes as `docker/model_files.sha256`, which the image build
  checks inside the image. Check a clone with:

  ```bash
  sha256sum --strict --check models/MODELS.sha256
  ```

- `licenses/`: the licence texts for the third-party files.

Every file is under 10 MiB, so Git LFS is not used.

| Files | What it is | Source | Licence |
|---|---|---|---|
| `artifacts/phase2m_g/runs/BCFINAL_seed3101/final.weights.h5` | Fixed14 BC-final recognizer (the shipped reader) | Trained in this project | Project's own; redistribution approved by the owner |
| `artifacts/phase2l_b/models/candidate_v2_conditioned_160x576_seed271828/final.weights.h5` | Fixed14 v2 recognizer (used by the pipeline's checks) | Trained in this project | Project's own; redistribution approved by the owner |
| `artifacts/phase2m_c/frozen/fixed14_v3.weights.h5` | Fixed14 v3 recognizer (used by the pipeline's checks) | Trained in this project | Project's own; redistribution approved by the owner |
| `artifacts/arabic_PP-OCRv5_mobile_rec_infer/` (`inference.json`, `.pdiparams`, `.yml`) | PaddleOCR Arabic text recognizer, used for orientation | PaddleOCR official inference model `arabic_PP-OCRv5_mobile_rec_infer.tar` (paddle-model-ecology.bj.bcebos.com, paddle3.0.0) | Apache-2.0, `licenses/PaddleOCR-Apache-2.0.txt` |
| `artifacts/latin_PP-OCRv3_mobile_rec_infer/` (`inference.json`, `.pdiparams`, `.yml`) | PaddleOCR Latin text recognizer, used for orientation | PaddleOCR official inference model `latin_PP-OCRv3_mobile_rec_infer.tar` (same host) | Apache-2.0, `licenses/PaddleOCR-Apache-2.0.txt` |
| `third_party/DocCornerNet-CoordClass/` (`model.py`, `LICENSE`, checkpoint `config.json` and `best_model.weights.h5`) | Card-corner detector | DocCornerNet-CoordClass | MIT, `third_party/DocCornerNet-CoordClass/LICENSE` |

Apache-2.0 and MIT both allow redistribution when the licence text is kept with
the files. No file needs a separate download step.

Not shipped and not needed by the image:

- `paddleocr_fixed_crop_bundle/`, a third-party bundle that is ignored by git.
- The Windows fonts that some development tests render with (arial, tahoma,
  trado, majalla, consola, times). Microsoft's licence does not allow
  redistributing them, and the image contains no font. Only those tests need them;
  see README_SERVICE.md section 11.
