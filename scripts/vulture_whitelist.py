"""Stage 7: names vulture reports as unused in the shipped code, and why each one is not dead.

vulture reads this file as code that "uses" each name. scripts/lint_shipped.sh passes it with
src/, scripts/ and tests/, so a finding in a shipped file means nothing in the repository uses it.
"""
# ruff: noqa
from src.extraction import cross_checks, dates, diagnose
from src.service import __main__ as service_main
from src.service import app, decider, recognizer, runtime

service_main.app_factory  # uvicorn imports it by the string "src.service.__main__:app_factory"
# endpoints and handlers nested in app.create_app, registered on the FastAPI app by decorators
app.create_app.health
app.create_app.http_error
app.create_app.validation_error
# response fields of VerifyResponse and VersionResponse: filled by keyword and serialised by FastAPI
app.VerifyResponse.model_version
app.VerifyResponse.decision_rule_version
app.VerifyResponse.pipeline_version
app.VerifyResponse.processing_ms
# fields of the Recognition record: read by the CLI's rows, the harnesses and the tests
recognizer.Recognition.decode_format
recognizer.Recognition.processing_ms
runtime.Loader  # used inside typing.cast("Loader", ...) and TYPE_CHECKING only
runtime.ModuleSpec  # used inside typing.cast("ModuleSpec", ...) and TYPE_CHECKING only
decider.DECISIONS  # the contract's four decision values: the service's public vocabulary
dates.PRECISIONS  # the vocabulary of printed-date precisions, documenting the module
diagnose.CAUSES  # the vocabulary of failure causes, documenting the module
cross_checks.Observations.printed_birth_date_precision  # a field of a public record
# Pydantic consumes these engine schema attributes; back_b64 is deliberately ignored.
model_config
back_b64
