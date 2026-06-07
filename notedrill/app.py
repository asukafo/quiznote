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

    # CSRF protection middleware
    @app.middleware("http")
    async def csrf_middleware(request: Request, call_next):
        if request.method in ("POST", "PUT", "PATCH", "DELETE"):
            origin = request.headers.get("origin", "")
            referer = request.headers.get("referer", "")
            host = request.headers.get("host", "")

            # Allow if same-origin
            is_same_origin = False
            for header_val in (origin, referer):
                if not header_val:
                    continue
                try:
                    parsed = urlparse(header_val)
                    if parsed.hostname == host.split(":")[0] or parsed.hostname in ("127.0.0.1", "localhost"):
                        is_same_origin = True
                        break
                except Exception:
                    pass

            # Also allow if no Origin/Referer (e.g., CLI tools, direct API calls)
            if not origin and not referer:
                is_same_origin = True

            if not is_same_origin:
                return JSONResponse(
                    status_code=403,
                    content={"detail": "CSRF validation failed"},
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
