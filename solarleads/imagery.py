"""Step 4a: grab a public-domain USDA NAIP aerial photo of each site from the USGS National Map."""
import logging
import os

import requests

from .config import settings
from .geo import bbox_around

log = logging.getLogger(__name__)


def naip_image(lat: float, lon: float, half_width_m: float = 70, px: int = 768, cache_key: str | None = None) -> bytes | None:
    """JPEG bytes of the roof area around (lat, lon), or None if no imagery is available there."""
    path = None
    if cache_key and settings.cache_images:
        path = os.path.join(settings.data_dir, "images", f"{cache_key}.jpg")
        if os.path.exists(path):
            with open(path, "rb") as f:
                return f.read()
    w, s, e, n = bbox_around(lat, lon, half_width_m)
    params = {
        "bbox": f"{w},{s},{e},{n}",
        "bboxSR": 4326,
        "imageSR": 3857,
        "size": f"{px},{px}",
        "format": "jpg",
        "renderingRule": '{"rasterFunction":"NaturalColor"}',
        "f": "image",
    }
    for attempt in range(3):
        try:
            r = requests.get(settings.naip_url, params=params, timeout=60, headers={"User-Agent": settings.user_agent})
            ctype = r.headers.get("content-type", "")
            if r.status_code == 200 and ctype.startswith("image") and len(r.content) > 5000:
                if path:
                    os.makedirs(os.path.dirname(path), exist_ok=True)
                    with open(path, "wb") as f:
                        f.write(r.content)
                return r.content
            log.debug("NAIP %s %s at %s,%s", r.status_code, ctype, lat, lon)
        except requests.RequestException as e:
            log.warning("NAIP error at %s,%s: %s", lat, lon, e)
    return None
