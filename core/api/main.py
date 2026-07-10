"""FastAPI application entrypoint — see docs/25-implementation-starter-kit/10-api-build-order.md."""

from fastapi import FastAPI

from core.common.errors import register_exception_handlers

app = FastAPI(title="Growth Operator")
register_exception_handlers(app)
