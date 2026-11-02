"""Serve the admin dashboard (app/dashboard) at /dashboard.

It is plain HTML, CSS and JavaScript calling this API from the same origin, so
it ships with every backend deploy and needs no CORS entry. Sign-in uses the
existing /auth/token endpoint; only admin accounts can use the pages.
"""

from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles

DASHBOARD_DIR = Path(__file__).resolve().parent.parent / "dashboard"
DASHBOARD_PATH = "/dashboard"

# Everything the dashboard loads comes from this origin, so the policy can be
# strict: no third-party scripts can run next to the admin's access token.
_SECURITY_HEADERS = {
    "Content-Security-Policy": (
        "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
        "connect-src 'self'; font-src 'self'; object-src 'none'; base-uri 'none'; "
        "form-action 'self'; frame-ancestors 'none'"
    ),
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    # Revalidate on every load so a deploy is picked up immediately.
    "Cache-Control": "no-cache",
}


def mount_dashboard(app: FastAPI) -> None:
    if not DASHBOARD_DIR.is_dir():
        return
    app.mount(DASHBOARD_PATH, StaticFiles(directory=DASHBOARD_DIR, html=True), name="dashboard")

    @app.middleware("http")
    async def dashboard_security_headers(request: Request, call_next):
        response = await call_next(request)
        if request.url.path == DASHBOARD_PATH or request.url.path.startswith(f"{DASHBOARD_PATH}/"):
            for name, value in _SECURITY_HEADERS.items():
                response.headers.setdefault(name, value)
        return response
