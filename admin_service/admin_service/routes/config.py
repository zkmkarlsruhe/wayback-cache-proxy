"""Config editing routes."""

from fastapi import APIRouter, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse

from ..config import load_yaml, save_yaml
from ..redis_client import publish_reload

router = APIRouter(prefix="/config")


@router.get("/", response_class=HTMLResponse)
async def config_page(request: Request):
    admin_config = request.app.state.admin_config
    try:
        data = load_yaml(admin_config.config_path)
    except FileNotFoundError:
        data = {}

    templates = request.app.state.templates
    return templates.TemplateResponse("config.html", {
        "request": request,
        "config": data,
        "saved": False,
    })


@router.post("/", response_class=HTMLResponse)
async def config_save(request: Request):
    admin_config = request.app.state.admin_config
    form = await request.form()

    # Load existing config as base
    try:
        data = load_yaml(admin_config.config_path)
    except FileNotFoundError:
        data = {}

    # Map form fields back to YAML structure
    # Form fields are named like "section.key" (e.g. "wayback.target_date")
    for field_name, value in form.items():
        if "." not in field_name:
            continue
        section, key = field_name.split(".", 1)
        if section not in data:
            data[section] = {}

        # Type coercion based on value content
        if value.lower() in ("true", "false"):
            value = value.lower() == "true"
        else:
            try:
                value = int(value)
            except (ValueError, TypeError):
                pass

        data[section][key] = value

    # Save and signal reload
    save_yaml(admin_config.config_path, data)
    await publish_reload(admin_config.redis_url)

    # Re-read admin password in case it changed
    admin_section = data.get("admin", {})
    if "password" in admin_section:
        admin_config.admin_password = admin_section["password"]

    templates = request.app.state.templates
    return templates.TemplateResponse("config.html", {
        "request": request,
        "config": data,
        "saved": True,
    })
