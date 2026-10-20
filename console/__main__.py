"""``python -m console``: run the console, manage accounts, or start a demonstration.

    python -m console serve                 the web application (settings from CONSOLE_* variables)
    python -m console create-user NAME      an account; the password is asked twice, never echoed
    python -m console demo                  an in-memory demonstration with synthetic records
    python -m console check                 verify connections and data shape (counts only, no IDs)
"""
from __future__ import annotations

import argparse
import getpass
import random
import sys

from . import config, security
from .services import Engine, Health, Inspector, MediaHost, Reread
from .store import Store


class DemoEngine:
    """Answers like the OCR engine, from the demonstration's hidden truth."""

    def __init__(self, truth: dict) -> None:
        self.truth = truth

    def reread(self, image: bytes, key: str) -> Reread:
        entry = self.truth.get(key, {})
        rng = random.Random(key)
        if entry.get("quality") in ("blur", "dark", "back"):
            return Reread(None, ["UNREADABLE_IMAGE"], None, "fixed14-bcfinal-3101", rng.uniform(0.6, 1.4))
        confidence = rng.uniform(0.35, 0.95) if entry.get("quality") == "glare" else rng.uniform(0.97, 0.999)
        return Reread(entry.get("card"), [], round(confidence, 4), "fixed14-bcfinal-3101", rng.uniform(0.4, 1.2))

    def health(self) -> Health:
        return Health(ok=True, detail="demo", model_version="fixed14-bcfinal-3101",
                      decision_rule_version="phase4-engine-v2", seconds=0.04)


def serve(settings: config.Settings) -> None:
    import uvicorn

    from .app import create_app

    if settings.demo:
        from .demo import DemoImages, DemoInspector, make_database

        database, truth = make_database(settings)
        store = Store(settings, database)

        def reading_of(key: str) -> str | None:
            record = store.record(key)
            return record.read if record else None

        inspector = Inspector(settings) if settings.inspector_url else DemoInspector(truth, reading_of)
        app = create_app(settings, store, DemoImages(truth), DemoEngine(truth), inspector)
    else:
        from pymongo import MongoClient

        client = MongoClient(settings.mongo_uri, tz_aware=True, serverSelectionTimeoutMS=8000)
        app = create_app(settings, Store(settings, client[settings.mongo_db]), MediaHost(settings), Engine(settings),
                         Inspector(settings) if settings.inspector_url else None)
    tls = {"ssl_certfile": settings.tls_cert, "ssl_keyfile": settings.tls_key} if settings.tls_cert else {}
    uvicorn.run(app, host=settings.host, port=settings.port, log_level="info", server_header=False,
                proxy_headers=False, **tls)


def create_user(settings: config.Settings, username: str, role: str, display_name: str) -> int:
    from pymongo import MongoClient

    store = Store(settings, MongoClient(settings.mongo_uri, tz_aware=True)[settings.mongo_db])
    store.ensure_indexes()
    if store.user(username):
        print(f"the account '{username}' already exists", file=sys.stderr)
        return 1
    password = getpass.getpass("password (at least 10 characters): ")
    if password != getpass.getpass("again: "):
        print("the two passwords differ", file=sys.stderr)
        return 1
    try:
        password_hash = security.hash_password(password)
    except ValueError as exc:
        print(exc, file=sys.stderr)
        return 1
    store.create_user(username, display_name or username, role, password_hash)
    store.audit("cli", "user_create", target=username.strip().lower(), detail={"role": role})
    print(f"created '{username.strip().lower()}' with role {role}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m console", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("serve", help="run the web application")
    user = sub.add_parser("create-user", help="create an account")
    user.add_argument("username")
    user.add_argument("--role", choices=security.ROLES, default="reviewer")
    user.add_argument("--name", default="", help="the name shown in the console")
    demo = sub.add_parser("demo", help="run with synthetic records in memory")
    demo.add_argument("--port", type=int, default=None)
    sub.add_parser("check", help="verify connections and data shape before first use (prints counts only)")
    args = parser.parse_args(argv)
    try:
        settings = config.load()
    except config.ConfigError as exc:
        print(f"configuration error: {exc}", file=sys.stderr)
        return 2
    if args.command == "serve":
        serve(settings)
    elif args.command == "check":
        from .check import main as check_main

        return check_main(settings)
    elif args.command == "create-user":
        return create_user(settings, args.username, args.role, args.name)
    else:
        from dataclasses import replace

        settings = replace(settings, demo=True, port=args.port or settings.port)
        print("building the synthetic demonstration database...", flush=True)
        print("accounts: admin / supervisor / reviewer, password: demo-password", flush=True)
        serve(settings)
    return 0


if __name__ == "__main__":
    sys.exit(main())
