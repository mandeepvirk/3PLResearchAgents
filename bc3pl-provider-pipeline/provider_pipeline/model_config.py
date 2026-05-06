from __future__ import annotations

import os


FAST_MODEL_ENV = "OPENAI_MODEL_FAST"
REVIEW_MODEL_ENV = "OPENAI_MODEL_REVIEW"
LEGACY_MODEL_ENV = "OPENAI_MODEL"

DEFAULT_FAST_MODEL = "gpt-5-mini"
DEFAULT_REVIEW_MODEL = "gpt-5.2"


def fast_model() -> str:
    return (
        os.getenv(FAST_MODEL_ENV)
        or os.getenv(LEGACY_MODEL_ENV)
        or DEFAULT_FAST_MODEL
    )


def review_model() -> str:
    return os.getenv(REVIEW_MODEL_ENV) or DEFAULT_REVIEW_MODEL
