"""Fixed14 review console: the operations and human-review web application.

It runs beside the OCR service and the team's MongoDB, and never changes either:
the OCR results the API client wrote stay as they are, and every reviewer
decision goes to its own collections (reviews, audit log, users, sessions).

Entry points: ``python -m console serve`` (the web application),
``python -m console create-user`` (accounts) and ``python -m console demo``
(an in-memory database filled with synthetic records, for a demonstration).
"""
