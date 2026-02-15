"""Login/logout routes."""

from fastapi import APIRouter, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse

from ..auth import create_session_cookie, clear_session_cookie

router = APIRouter()


@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request, error: str = ""):
    templates = request.app.state.templates
    return templates.TemplateResponse("login.html", {
        "request": request,
        "error": error,
    })


@router.post("/login")
async def login_submit(request: Request, password: str = Form(...)):
    admin_config = request.app.state.admin_config
    if password != admin_config.admin_password:
        templates = request.app.state.templates
        return templates.TemplateResponse("login.html", {
            "request": request,
            "error": "Invalid password",
        }, status_code=401)

    response = RedirectResponse("/", status_code=303)
    create_session_cookie(response)
    return response


@router.get("/logout")
async def logout(request: Request):
    response = RedirectResponse("/login", status_code=303)
    clear_session_cookie(response)
    return response
