from __future__ import annotations

import base64
import io
import json
from pathlib import Path

import httpx
from PIL import Image

from .config import settings


def _config_path() -> Path:
    if settings.database_url.startswith("sqlite:///"):
        database = Path(settings.database_url.removeprefix("sqlite:///"))
        if not database.is_absolute():
            database = Path(__file__).resolve().parents[1] / database
        return database.parent / "portrait-provider.json"
    return Path(__file__).resolve().parents[1] / "data" / "portrait-provider.json"


def local_config() -> dict:
    if not settings.local_mode:
        return {}
    try:
        value = json.loads(_config_path().read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError, TypeError):
        return {}


def save_local_config(provider: str, comfyui_url: str = "", openai_api_key: str = "",
                      openai_image_model: str = "") -> dict:
    if not settings.local_mode:
        raise RuntimeError("Hosted portrait settings are controlled by private deployment variables.")
    provider = provider.casefold().strip()
    if provider not in {"manual", "comfyui", "openai"}:
        raise ValueError("Choose manual uploads, Local AI (ComfyUI), or OpenAI.")
    current = local_config()
    value = {
        "provider": provider,
        "comfyui_url": comfyui_url.strip().rstrip("/") or current.get("comfyui_url") or settings.comfyui_url,
        "openai_api_key": openai_api_key.strip() or current.get("openai_api_key", ""),
        "openai_image_model": openai_image_model.strip() or current.get("openai_image_model") or settings.openai_image_model,
    }
    path = _config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2), encoding="utf-8")
    return value


def effective_config() -> dict:
    local = local_config()
    return {
        "provider": str(local.get("provider") or settings.portrait_provider).casefold(),
        "comfyui_url": str(local.get("comfyui_url") or settings.comfyui_url).rstrip("/"),
        "openai_api_key": str(local.get("openai_api_key") or settings.openai_api_key),
        "openai_image_model": str(local.get("openai_image_model") or settings.openai_image_model),
    }


def provider_status() -> dict:
    config = effective_config()
    provider = config["provider"]
    return {
        "provider": provider,
        "available": provider == "manual"
        or (provider == "comfyui" and bool(config["comfyui_url"]))
        or (provider == "openai" and bool(config["openai_api_key"])),
        "cost": "none/local" if provider in {"manual", "comfyui"} else "provider API usage",
        "comfyui_url": config["comfyui_url"],
        "has_openai_key": bool(config["openai_api_key"]),
        "local_editable": settings.local_mode,
    }


def test_provider() -> dict:
    config = effective_config()
    provider = config["provider"]
    if provider == "manual":
        return {"ok": True, "message": "Manual portrait uploads are ready."}
    if provider == "openai":
        if not config["openai_api_key"]:
            return {"ok": False, "message": "Add an OpenAI API key first."}
        return {"ok": True, "message": f"OpenAI is configured with {config['openai_image_model']}."}
    try:
        response = httpx.get(f"{config['comfyui_url']}/system_stats", timeout=4)
        response.raise_for_status()
        return {"ok": True, "message": "Local ComfyUI is running and reachable."}
    except Exception as exc:
        return {"ok": False, "message": f"Local ComfyUI was not reachable: {str(exc)[:140]}"}


def normalize_image(data: bytes, max_pixels: int = 1600) -> tuple[bytes, str]:
    image = Image.open(io.BytesIO(data)).convert("RGB")
    image.thumbnail((max_pixels, max_pixels))
    output = io.BytesIO()
    image.save(output, format="WEBP", quality=88, method=6)
    return output.getvalue(), "image/webp"


def prompt(first: str, second: str, year: int) -> str:
    return (
        f"Formal marriage portrait of {first} and {second}, married in {year}. "
        "Preserve both identities from the reference portraits. Use historically accurate "
        "clothing, textiles, hair, setting and material culture for that exact year. No text or watermark."
    )


def generate(first_image: bytes, second_image: bytes, first: str, second: str, year: int) -> bytes:
    config = effective_config()
    provider = config["provider"]
    if provider == "comfyui":
        request = {
            "prompt": prompt(first, second, year),
            "images": [base64.b64encode(first_image).decode(), base64.b64encode(second_image).decode()],
        }
        response = httpx.post(f"{config['comfyui_url']}/decades/generate", json=request, timeout=180)
        response.raise_for_status()
        return base64.b64decode(response.json()["image"])
    if provider == "openai":
        from openai import OpenAI
        client = OpenAI(api_key=config["openai_api_key"])
        response = client.images.edit(
            model=config["openai_image_model"],
            image=[io.BytesIO(first_image), io.BytesIO(second_image)],
            prompt=prompt(first, second, year), size="1024x1024",
        )
        return base64.b64decode(response.data[0].b64_json)
    raise RuntimeError("Automatic generation is off. Upload a portrait or enable local ComfyUI in Settings.")
