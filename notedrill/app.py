"""FastAPI web interface — NoteDrill v0.4.

Four learning modes:
  Quiz    — Generate & answer questions (SM-2 spaced repetition)
  Present — Knowledge outline: user fills in, AI reviews
  DeepDive — Progressive follow-up questions, drill deeper
  Review  — SM-2 due cards only
"""

from __future__ import annotations

from pathlib import Path
from urllib.parse import urlparse

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

STATIC_DIR = Path(__file__).parent / "static"


def create_app() -> FastAPI:
    app = FastAPI(title="NoteDrill", version="0.4.0")

    if STATIC_DIR.exists():
        app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    # CSRF protection — only allow same-origin or localhost requests
    @app.middleware("http")
    async def csrf_middleware(request: Request, call_next):
        if request.method in ("POST", "PUT", "PATCH", "DELETE"):
            host = request.headers.get("host", "")
            # Allow localhost-only. For a single-user desktop tool, CSRF is not a
            # realistic threat vector; this guard prevents accidental exposure.
            if host and host.split(":")[0] not in ("127.0.0.1", "localhost", "::1"):
                origin = request.headers.get("origin", "")
                referer = request.headers.get("referer", "")
                seen = set()
                for header_val in (origin, referer):
                    if header_val and header_val not in seen:
                        seen.add(header_val)
                        try:
                            parsed = urlparse(header_val)
                            if parsed.hostname == host.split(":")[0]:
                                break
                        except Exception:
                            pass
                else:
                    if origin or referer:
                        return JSONResponse(
                            status_code=403,
                            content={"detail": "Cross-origin requests are not allowed"},
                        )

        response = await call_next(request)
        return response

    # Register all route modules
    from .routes.vault import register_routes as register_vault
    from .routes.generate import register_routes as register_generate
    from .routes.present import register_routes as register_present
    from .routes.deepdive import register_routes as register_deepdive
    from .routes.review import register_routes as register_review
    from .routes.quiz_legacy import register_routes as register_quiz
    from .routes.questions import register_routes as register_questions

    register_vault(app)
    register_generate(app)
    register_present(app)
    register_deepdive(app)
    register_review(app)
    register_quiz(app)
    register_questions(app)

    return app
