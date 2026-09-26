import logging
import re

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import get_settings
from app.routers.api import router


class _RedactingFilter(logging.Filter):
    """Belt-and-braces: strip anything that looks like an email or phone number from log messages."""

    _patterns = [re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+"), re.compile(r"(?:\+|00)\d[\d \-]{6,}\d")]

    def filter(self, record: logging.LogRecord) -> bool:
        msg = record.getMessage()
        for p in self._patterns:
            msg = p.sub("[redacted]", msg)
        record.msg, record.args = msg, ()
        return True


logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
for handler in logging.getLogger().handlers:
    handler.addFilter(_RedactingFilter())

app = FastAPI(
    title="Mergero Company Database API",
    version="0.1.0",
    description="Permission-gated, provenance-linked company database for Nordic and DACH ingestion.",
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in get_settings().cors_origins.split(",") if o.strip()],
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(router)
