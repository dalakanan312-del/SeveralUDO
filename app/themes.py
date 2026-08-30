from __future__ import annotations

import re


_HEX = re.compile(r"^#[0-9a-fA-F]{6}$")

PRESETS = {
    "heirloom": {
        "name": "Heirloom Gold",
        "description": "The familiar dark archive with warm gold details.",
        "accent": "#c99a4d", "background": "#0d0f13", "surface": "#181a1f",
        "text": "#f1eadf", "muted": "#aaa398", "mode": "dark",
    },
    "midnight": {
        "name": "Midnight Ledger",
        "description": "Deep navy pages with cool blue highlights.",
        "accent": "#86a9dc", "background": "#080d18", "surface": "#111a2a",
        "text": "#eef4ff", "muted": "#a4b0c4", "mode": "dark",
    },
    "forest": {
        "name": "Heritage Green",
        "description": "A quiet forest palette suited to long family chronicles.",
        "accent": "#a8b879", "background": "#0b120e", "surface": "#151e18",
        "text": "#edf2e7", "muted": "#a9b5a5", "mode": "dark",
    },
    "plum": {
        "name": "Regency Plum",
        "description": "Soft purple accents over a deep aubergine archive.",
        "accent": "#c9a0d2", "background": "#120d15", "surface": "#1e1622",
        "text": "#f4ebf5", "muted": "#b7a9ba", "mode": "dark",
    },
    "oxblood": {
        "name": "Oxblood Chronicle",
        "description": "Warm red-brown panels inspired by leather-bound books.",
        "accent": "#cf927d", "background": "#150c0d", "surface": "#211416",
        "text": "#f5eae3", "muted": "#bba7a1", "mode": "dark",
    },
    "parchment": {
        "name": "Sepia Manuscript",
        "description": "A warmer brown-black archive inspired by aged paper and leather.",
        "accent": "#d2a15c", "background": "#15100b", "surface": "#251d14",
        "text": "#f3e8d4", "muted": "#b9aa92", "mode": "dark",
    },
}

DEFAULTS = {
    "preset": "heirloom",
    "density": "comfortable",
    "text_scale": "standard",
    "heading_style": "classic",
    "corners": "soft",
    "reduce_motion": False,
}


def _hex(value: object, fallback: str) -> str:
    candidate = str(value or "").strip().lower()
    return candidate if _HEX.fullmatch(candidate) else fallback


def _rgb(value: str) -> tuple[int, int, int]:
    return tuple(int(value[index:index + 2], 16) for index in (1, 3, 5))


def _hex_from(rgb: tuple[int, int, int]) -> str:
    return "#" + "".join(f"{max(0, min(255, channel)):02x}" for channel in rgb)


def _mix(first: str, second: str, amount: float) -> str:
    left, right = _rgb(first), _rgb(second)
    return _hex_from(tuple(round(a + (b - a) * amount) for a, b in zip(left, right)))


def _luminance(value: str) -> float:
    channels = []
    for channel in _rgb(value):
        normalized = channel / 255
        channels.append(normalized / 12.92 if normalized <= .04045 else ((normalized + .055) / 1.055) ** 2.4)
    return .2126 * channels[0] + .7152 * channels[1] + .0722 * channels[2]


def contrast(first: str, second: str) -> float:
    high, low = sorted((_luminance(first), _luminance(second)), reverse=True)
    return (high + .05) / (low + .05)


def _accessible_text(background: str, requested: str) -> tuple[str, bool]:
    if contrast(background, requested) >= 4.5:
        return requested, False
    candidates = ("#f8f5ee", "#171512")
    replacement = max(candidates, key=lambda color: contrast(background, color))
    return replacement, True


def _dark_canvas(value: str, limit: float) -> tuple[str, bool]:
    original = value
    while _luminance(value) > limit:
        value = _mix(value, "#000000", .18)
    return value, value != original


def resolve(value: object = None) -> dict:
    raw = value if isinstance(value, dict) else {}
    preset_key = str(raw.get("preset") or DEFAULTS["preset"]).casefold()
    preset = PRESETS.get(preset_key, PRESETS["heirloom"])
    if preset_key not in PRESETS and preset_key != "custom":
        preset_key = "heirloom"

    custom = preset_key == "custom"
    source = raw if custom else preset
    accent = _hex(source.get("accent"), preset["accent"])
    background = _hex(source.get("background"), preset["background"])
    surface = _hex(source.get("surface"), preset["surface"])
    background, background_corrected = _dark_canvas(background, .055)
    surface, surface_corrected = _dark_canvas(surface, .085)
    requested_text = _hex(source.get("text"), preset["text"])
    text, text_corrected = _accessible_text(surface, requested_text)
    mode = "dark"
    muted_default = _mix(text, surface, .38)
    muted = _hex(source.get("muted"), muted_default)
    if contrast(surface, muted) < 3:
        muted = muted_default

    density = str(raw.get("density") or DEFAULTS["density"]).casefold()
    text_scale = str(raw.get("text_scale") or DEFAULTS["text_scale"]).casefold()
    heading_style = str(raw.get("heading_style") or DEFAULTS["heading_style"]).casefold()
    corners = str(raw.get("corners") or DEFAULTS["corners"]).casefold()
    if density not in {"comfortable", "compact"}: density = DEFAULTS["density"]
    if text_scale not in {"small", "standard", "large"}: text_scale = DEFAULTS["text_scale"]
    if heading_style not in {"classic", "modern", "bookish"}: heading_style = DEFAULTS["heading_style"]
    if corners not in {"square", "soft", "round"}: corners = DEFAULTS["corners"]

    raised = _mix(surface, "#ffffff", .055)
    panel_soft = _mix(background, surface, .52)
    line = _mix(surface, text, .17)
    gold_bright = _mix(accent, "#ffffff", .32)
    gold_dim = _mix(accent, background, .48)
    radius = {"square": "5px", "soft": "15px", "round": "24px"}[corners]
    body_size = {"small": "13.5px", "standard": "14.5px", "large": "16px"}[text_scale]
    heading_font = {
        "classic": "Georgia, 'Times New Roman', serif",
        "modern": "Inter, 'Segoe UI', sans-serif",
        "bookish": "'Palatino Linotype', Palatino, Georgia, serif",
    }[heading_style]
    accent_rgb = ",".join(map(str, _rgb(accent)))
    background_rgb = ",".join(map(str, _rgb(background)))
    surface_rgb = ",".join(map(str, _rgb(surface)))
    inline_style = ";".join((
        f"--ink:{text}", f"--text:{text}", f"--muted:{muted}",
        f"--paper:{background}", f"--panel:{surface}", f"--panel-raised:{raised}",
        f"--panel-soft:{panel_soft}", f"--surface-2:{raised}", f"--line:{line}",
        f"--line-soft:rgba({surface_rgb},.10)", f"--gold:{accent}",
        f"--gold-bright:{gold_bright}", f"--gold-dim:{gold_dim}",
        f"--accent-rgb:{accent_rgb}", f"--paper-rgb:{background_rgb}",
        f"--panel-rgb:{surface_rgb}", f"--radius:{radius}", f"--theme-body-size:{body_size}",
        f"--theme-heading-font:{heading_font}",
    ))
    return {
        "preset": preset_key, "name": preset.get("name", "Custom theme") if not custom else "Custom theme",
        "accent": accent, "background": background, "surface": surface, "text": text,
        "muted": muted, "mode": mode, "density": density, "text_scale": text_scale,
        "heading_style": heading_style, "corners": corners,
        "reduce_motion": bool(raw.get("reduce_motion", DEFAULTS["reduce_motion"])),
        "inline_style": inline_style, "text_corrected": text_corrected,
        "canvas_corrected": background_corrected or surface_corrected,
        "contrast": round(contrast(surface, text), 2),
    }


def from_form(form) -> dict:
    preset = str(form.get("theme_preset") or DEFAULTS["preset"]).casefold()
    if preset not in PRESETS and preset != "custom": preset = DEFAULTS["preset"]
    raw = {
        "preset": preset,
        "accent": form.get("theme_accent"),
        "background": form.get("theme_background"),
        "surface": form.get("theme_surface"),
        "text": form.get("theme_text"),
        "muted": form.get("theme_muted"),
        "density": form.get("theme_density"),
        "text_scale": form.get("theme_text_scale"),
        "heading_style": form.get("theme_heading_style"),
        "corners": form.get("theme_corners"),
        "reduce_motion": "theme_reduce_motion" in form,
    }
    resolved = resolve(raw)
    return {key: resolved[key] for key in (
        "preset", "accent", "background", "surface", "text", "muted", "density",
        "text_scale", "heading_style", "corners", "reduce_motion",
    )}


def presets_for_ui() -> list[dict]:
    return [{"id": key, **value, "resolved": resolve({"preset": key})} for key, value in PRESETS.items()]
