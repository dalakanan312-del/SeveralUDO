from __future__ import annotations

import base64
import os


def configured():
    return bool(os.environ.get("OPENAI_API_KEY", "").strip())


def _data_url(photo):
    mime = photo["mime_type"] or "image/jpeg"
    encoded = base64.b64encode(bytes(photo["image_data"])).decode("ascii")
    return f"data:{mime};base64,{encoded}"


def build_prompt(partner1_name, partner2_name, marriage_year):
    return f"""Create one tasteful, photorealistic formal marriage portrait of {partner1_name} and {partner2_name} in the year {marriage_year}.
The first reference image identifies {partner1_name}; the second identifies {partner2_name}. Preserve each person's distinct face, skin tone, hair, apparent age, body characteristics, and any fantasy or occult traits. Do not merge their faces or change their identities.
Dress both people in historically plausible formal wedding clothing for {marriage_year}, including period-accurate silhouettes, textiles, tailoring, accessories, hairstyles, and grooming. Avoid modern clothing or objects. Use a dignified waist-up paired composition, natural expressions, flattering period-appropriate lighting, and a simple setting suitable to the era. No text, borders, logos, or watermarks."""


def generate_portrait(partner1_photo, partner2_photo, partner1_name, partner2_name, marriage_year):
    if not configured():
        raise RuntimeError("OPENAI_API_KEY is not configured.")

    from openai import OpenAI

    client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])
    response = client.responses.create(
        model=os.environ.get("OPENAI_IMAGE_TOOL_MODEL", "gpt-5.6"),
        input=[{
            "role": "user",
            "content": [
                {"type": "input_text", "text": build_prompt(partner1_name, partner2_name, marriage_year)},
                {"type": "input_image", "image_url": _data_url(partner1_photo)},
                {"type": "input_image", "image_url": _data_url(partner2_photo)},
            ],
        }],
        tools=[{"type": "image_generation"}],
    )
    for item in response.output:
        if getattr(item, "type", None) == "image_generation_call" and getattr(item, "result", None):
            return base64.b64decode(item.result)
    raise RuntimeError("The image service did not return a portrait. Please try again.")
