"""Local evaluation UI; registered only when development docs are enabled."""

from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import FileResponse

router = APIRouter(include_in_schema=False)
STATIC_DIR = Path(__file__).resolve().parents[2] / "static" / "chat"
HEADERS = {
    "Content-Security-Policy": (
        "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; "
        "img-src 'self' data:; frame-ancestors 'none'; object-src 'none'; "
        "base-uri 'none'; form-action 'none'"
    ),
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "Cache-Control": "no-store",
}


@router.get("/chat")
@router.get("/chat/")
async def chat_page() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html", media_type="text/html", headers=HEADERS)


@router.get("/chat/app.js")
async def chat_script() -> FileResponse:
    return FileResponse(STATIC_DIR / "app.js", media_type="text/javascript", headers=HEADERS)


@router.get("/chat/style.css")
async def chat_styles() -> FileResponse:
    return FileResponse(STATIC_DIR / "style.css", media_type="text/css", headers=HEADERS)
