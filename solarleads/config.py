"""Settings, read from environment variables so the same code runs locally and on GitHub Actions."""
import os
from dataclasses import dataclass, field


def _int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except ValueError:
        return default


@dataclass
class Settings:
    # Data sources
    overpass_url: str = os.environ.get("OVERPASS_URL", "https://overpass-api.de/api/interpreter")
    naip_url: str = os.environ.get(
        "NAIP_URL",
        "https://imagery.nationalmap.gov/arcgis/rest/services/USGSNAIPImagery/ImageServer/exportImage",
    )
    regrid_url: str = os.environ.get("REGRID_URL", "https://app.regrid.com/api/v2/parcels/point")

    # Keys (leave blank to skip that step)
    regrid_token: str = os.environ.get("REGRID_TOKEN", "")
    anthropic_api_key: str = os.environ.get("ANTHROPIC_API_KEY", "")
    vision_model: str = os.environ.get("VISION_MODEL", "claude-haiku-5-5")

    # Behavior
    tile_degrees: float = float(os.environ.get("TILE_DEGREES", "1.0"))
    overpass_pause_s: float = float(os.environ.get("OVERPASS_PAUSE_S", "2"))
    cluster_meters: float = float(os.environ.get("CLUSTER_METERS", "60"))
    min_cluster_m2: float = float(os.environ.get("MIN_CLUSTER_M2", "120"))  # below this, needs a business nearby to count
    place_match_meters: float = float(os.environ.get("PLACE_MATCH_METERS", "60"))  # first-pass business search
    use_buildings: bool = os.environ.get("USE_BUILDINGS", "1") == "1"  # snap panels to building footprints
    building_cell_deg: float = float(os.environ.get("BUILDING_CELL_DEG", "0.1"))
    building_near_m: float = float(os.environ.get("BUILDING_NEAR_M", "20"))  # carport/ground arrays: nearest building
    outside_match_m: float = float(os.environ.get("OUTSIDE_MATCH_M", "25"))  # business just outside the building
    max_roof_checks: int = _int("MAX_ROOF_CHECKS", 2000)  # spending cap per run
    max_regrid_lookups: int = _int("MAX_REGRID_LOOKUPS", 5000)  # spending cap per run
    workers: int = _int("WORKERS", 6)
    cache_images: bool = os.environ.get("CACHE_IMAGES", "0") == "1"  # keep aerial photos on disk (local runs)

    # Files
    data_dir: str = os.environ.get("DATA_DIR", "data")
    out_dir: str = os.environ.get("OUT_DIR", "output")
    user_agent: str = os.environ.get(
        "USER_AGENT", "solar-leads/1.0 (commercial solar lead finder; contact: info@clearrisetechnologies.com)"
    )

    @property
    def db_path(self) -> str:
        return os.path.join(self.data_dir, "solarleads.sqlite")


settings = Settings()
