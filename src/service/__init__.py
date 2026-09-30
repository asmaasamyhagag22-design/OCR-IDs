"""The Fixed14 OCR verification service and the competition CLI: one pipeline, two entry points.

- `recognizer`: image bytes in, a reading out (the only code that touches the models).
- `decider`: a reading and the typed ID in, a decision out (no model access).
- `app`: the FastAPI service implementing docs/release/ocr_api_contract_v1.md.
- `cli`: the competition CLI (an image folder in, a CSV out).

Declared in configs/phase4_service_rule_v1.json.
"""
