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


def open_image(data: bytes) -> Image.Image:
    """Load a portrait, including the separate alpha mask in Sims 4 JPEGs.

    The game's APP0 ALFA segment contains a big-endian PNG length followed by
    a grayscale PNG. Its red channel is opacity, not picture content. Ignoring
    it exposes the stretched edge colors the game keeps behind transparent
    pixels. Read the segment structure rather than searching compressed data.
    """
    with Image.open(io.BytesIO(data)) as source:
        alpha_segments = [payload for marker, payload in getattr(source, "applist", ())
                          if marker == "APP0" and payload.startswith(b"ALFA")]
        if alpha_segments:
            if len(alpha_segments) != 1:
                raise ValueError("The game portrait has conflicting transparency masks.")
            payload = alpha_segments[0]
            length = int.from_bytes(payload[4:8], "big")
            if (len(payload) < 16 or length != len(payload) - 8
                    or payload[8:16] != b"\x89PNG\r\n\x1a\n"):
                raise ValueError("The game portrait transparency mask is incomplete.")
            # Bound allocation before loading either image. Real Tray portraits
            # are normally 640-square; malformed masks must not allocate freely.
            if source.width * source.height > 4096 * 4096:
                raise ValueError("The game portrait is too large.")
            with Image.open(io.BytesIO(payload[8:]), formats=["PNG"]) as mask:
                if mask.size != source.size:
                    raise ValueError("The game portrait and transparency mask have different sizes.")
                image = source.convert("RGBA")
                image.putalpha(mask.convert("RGB").getchannel("R"))
                return image
        # Preserve ordinary PNG/WebP transparency too, including palette PNGs.
        mode = "RGBA" if "A" in source.getbands() or "transparency" in source.info else "RGB"
        return source.convert(mode)


def normalize_image(data: bytes, max_pixels: int = 1600, *, lossless: bool = False) -> tuple[bytes, str]:
    image = open_image(data)
    image.thumbnail((max_pixels, max_pixels), Image.Resampling.LANCZOS)
    output = io.BytesIO()
    image.save(output, format="WEBP", quality=88, method=6, lossless=lossless)
    return output.getvalue(), "image/webp"


def prompt(first: str, second: str, year: int) -> str:
    return (
        f"Formal marriage portrait of {first} and {second}, married in {year}. "
        "Preserve both identities from the reference portraits. Use historically accurate "
        "clothing, textiles, hair, setting and material culture for that exact year. No text or watermark."
    )


def generate_references(images: list[bytes], text: str, config: dict) -> bytes:
    """One deliberate edit request; do not silently retry a potentially paid job."""
    provider = config["provider"]
    if not config.get("enabled", provider != "manual"):
        raise ValueError("AI generation is off. Enable it in Portrait Studio → AI settings.")
    references = []
    for raw in images:
        output = io.BytesIO()
        open_image(raw).save(output, format="PNG")
        references.append(output.getvalue())
    if provider == "comfyui":
        request = {
            "prompt": text,
            "images": [base64.b64encode(raw).decode() for raw in references],
        }
        response = httpx.post(f"{config['comfyui_url']}/decades/generate", json=request, timeout=180)
        response.raise_for_status()
        return base64.b64decode(response.json()["image"], validate=True)
    if provider == "openai":
        from openai import OpenAI
        if not config.get("openai_api_key"):
            raise ValueError("Add an OpenAI API key in Portrait Studio → AI settings first.")
        options = {"input_fidelity": "high"} if config["openai_image_model"] == "gpt-image-1" else {}
        with OpenAI(api_key=config["openai_api_key"], timeout=240, max_retries=0) as client:
            response = client.images.edit(
                model=config["openai_image_model"],
                image=[(f"reference-{index}.png", raw, "image/png") for index, raw in enumerate(references)],
                prompt=text, size="1024x1024", n=1, **options,
            )
        if not response.data or not response.data[0].b64_json:
            raise ValueError("The image provider returned no image. No portrait was saved.")
        return base64.b64decode(response.data[0].b64_json, validate=True)
    raise ValueError("Choose an AI provider in Portrait Studio → AI settings.")


def generate(first_image: bytes, second_image: bytes, first: str, second: str, year: int, *, config: dict | None = None) -> bytes:
    return generate_references([first_image, second_image], prompt(first, second, year), config or effective_config())
