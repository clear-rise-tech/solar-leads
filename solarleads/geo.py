"""Small geometry helpers: distances, polygon area, grouping nearby arrays into one site."""
import math
from typing import Iterable

EARTH_R = 6371008.8


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * EARTH_R * math.asin(math.sqrt(a))


def polygon_area_m2(coords: list[tuple[float, float]]) -> float:
    """Area of a lat/lon ring (list of (lat, lon)) using a local flat projection. Good to a few % at roof scale."""
    if len(coords) < 3:
        return 0.0
    lat0 = sum(c[0] for c in coords) / len(coords)
    kx = math.cos(math.radians(lat0)) * EARTH_R * math.pi / 180
    ky = EARTH_R * math.pi / 180
    pts = [(c[1] * kx, c[0] * ky) for c in coords]
    s = 0.0
    for (x1, y1), (x2, y2) in zip(pts, pts[1:] + pts[:1]):
        s += x1 * y2 - x2 * y1
    return abs(s) / 2


def bbox_around(lat: float, lon: float, meters: float) -> tuple[float, float, float, float]:
    """(west, south, east, north) box of half-width `meters` around a point."""
    dlat = meters / 111_320
    dlon = meters / (111_320 * max(math.cos(math.radians(lat)), 0.01))
    return lon - dlon, lat - dlat, lon + dlon, lat + dlat


def tiles(west: float, south: float, east: float, north: float, step: float) -> Iterable[tuple[float, float, float, float]]:
    """Split a bounding box into step x step degree tiles (south, west, north, east order for Overpass)."""
    lat = south
    while lat < north:
        lon = west
        while lon < east:
            yield (round(lat, 4), round(lon, 4), round(min(lat + step, north), 4), round(min(lon + step, east), 4))
            lon += step
        lat += step


class GridIndex:
    """Bucket points into a lat/lon grid so 'what is near here' stays fast with millions of points."""

    def __init__(self, cell_deg: float = 0.01):
        self.cell = cell_deg
        self.buckets: dict[tuple[int, int], list] = {}

    def _key(self, lat: float, lon: float) -> tuple[int, int]:
        return int(math.floor(lat / self.cell)), int(math.floor(lon / self.cell))

    def add(self, lat: float, lon: float, item) -> None:
        self.buckets.setdefault(self._key(lat, lon), []).append((lat, lon, item))

    def near(self, lat: float, lon: float, meters: float):
        """Yield (distance_m, item) for items within `meters`, nearest first."""
        ky, kx = self._key(lat, lon)
        reach = max(1, int(math.ceil(meters / 111_320 / self.cell)) + 1)
        found = []
        for dy in range(-reach, reach + 1):
            for dx in range(-reach, reach + 1):
                for plat, plon, item in self.buckets.get((ky + dy, kx + dx), []):
                    d = haversine_m(lat, lon, plat, plon)
                    if d <= meters:
                        found.append((d, item))
        found.sort(key=lambda t: t[0])
        return found


def cluster_points(points: list[dict], meters: float) -> list[list[dict]]:
    """Group arrays that sit within `meters` of each other (single-link) into one site.

    Each point dict needs 'lat' and 'lon'. Returns a list of groups.
    """
    idx = GridIndex(cell_deg=max(meters / 111_320, 0.0005))
    for i, p in enumerate(points):
        idx.add(p["lat"], p["lon"], i)
    parent = list(range(len(points)))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for i, p in enumerate(points):
        for _, j in idx.near(p["lat"], p["lon"], meters):
            ri, rj = find(i), find(j)
            if ri != rj:
                parent[rj] = ri
    groups: dict[int, list[dict]] = {}
    for i, p in enumerate(points):
        groups.setdefault(find(i), []).append(p)
    return list(groups.values())
