"""Step 4b: ask Claude to look at the aerial photo and say whether the center building has solar panels
and what kind of building it is. Returns a small dict; never raises."""
import base64
import logging

from .config import settings

log = logging.getLogger(__name__)

TOOL = {
    "name": "report_roof",
    "description": (
        "Report what you see at the CENTER of this aerial photo (the site being checked). "
        "Call this exactly once."
    ),
    "strict": True,
    "input_schema": {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "panels": {
                "type": "string",
                "enum": ["yes", "no", "unclear"],
                "description": "Are solar panels (dark rectangular arrays, rooftop, carport or ground-mounted) visible at or right next to the center?",
            },
            "building_type": {
                "type": "string",
                "enum": ["house", "multifamily", "commercial", "industrial_warehouse", "institutional", "farm", "parking_or_ground", "unknown"],
                "description": "Best guess at the property type at the center. 'house' = detached single-family home.",
            },
            "roof_size": {
                "type": "string",
                "enum": ["small", "medium", "large"],
                "description": "small = house-sized, medium = typical store/office, large = warehouse/big box/campus.",
            },
            "note": {"type": "string", "description": "Under 15 words: anything useful for a sales call (e.g. 'carport array over parking lot')."},
        },
        "required": ["panels", "building_type", "roof_size", "note"],
    },
}

PROMPT = (
    "This is a top-down aerial photo, north up, roughly 140 meters across, centered on a location where solar panels "
    "were mapped. Look at the building or structure in the middle of the image. Panels look like dark blue/black "
    "rectangles in neat rows. Report using the report_roof tool."
)

_client = None


def _get_client():
    global _client
    if _client is None:
        import anthropic

        _client = anthropic.Anthropic(api_key=settings.anthropic_api_key, max_retries=4)
    return _client


def check_roof(jpeg: bytes) -> dict:
    if not settings.anthropic_api_key:
        return {"panels": "not_checked", "building_type": "unknown", "roof_size": "", "note": "no ANTHROPIC_API_KEY"}
    try:
        msg = _get_client().messages.create(
            model=settings.vision_model,
            max_tokens=1024,
            tools=[TOOL],
            messages=[{
                "role": "user",
                "content": [
                    {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg",
                                                  "data": base64.standard_b64encode(jpeg).decode()}},
                    {"type": "text", "text": PROMPT},
                ],
            }],
        )
        for block in msg.content:
            if block.type == "tool_use" and block.name == "report_roof":
                return dict(block.input)
        return {"panels": "unclear", "building_type": "unknown", "roof_size": "", "note": "model gave no answer"}
    except Exception as e:  # keep the run going; the site is marked for a manual look
        log.warning("Roof check failed: %s", e)
        return {"panels": "error", "building_type": "unknown", "roof_size": "", "note": str(e)[:80]}
