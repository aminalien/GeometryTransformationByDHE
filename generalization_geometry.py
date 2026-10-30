"""Geometry-only target generation for the interactive generalization workspace.

This module does not allocate DHE records or change the paper algorithms.
"""
from __future__ import annotations

from dataclasses import dataclass
from math import hypot, isfinite
from pathlib import Path
import json
import urllib.parse
import urllib.request
import urllib.error

import geopandas as gpd
import numpy as np
from shapely import force_2d
from shapely.geometry import Polygon, Point, LineString, box
from shapely.geometry.polygon import orient
from shapely.ops import polygonize, unary_union

DEFAULT_DATA = "C:/University/PhD/Data/Kleczkow Updated/osm_data1.shp"
TOL = 1e-7


def feature_color(index):
    """Stable colours independent of selection, feature removal, and history."""
    from colorsys import hsv_to_rgb
    return (*hsv_to_rgb((int(index)*.618033988749895)%1., .58, .80),1.)


@dataclass
class LoadedData:
    frame: gpd.GeoDataFrame
    rejected: list[str]
    source: str


def prepare_frame(frame, source="memory"):
    if frame.crs is None:
        raise ValueError("The dataset must declare its CRS.")
    frame = frame.copy()
    # Keep an existing metre CRS; geographic and other linear units use UTM.
    axes = frame.crs.axis_info
    if not frame.crs.is_projected or not axes or any(abs(a.unit_conversion_factor-1) > 1e-10 for a in axes[:2]):
        frame = frame.to_crs(frame.estimate_utm_crs())
    accepted, rejected = [], []
    for position, (_, row) in enumerate(frame.iterrows(), 1):
        geometry = row.geometry
        reason = None
        if geometry is None or geometry.is_empty:
            reason = "empty geometry"
        elif geometry.geom_type != "Polygon":
            reason = f"unsupported {geometry.geom_type}"
        elif not geometry.is_valid or geometry.area <= TOL*TOL:
            reason = "invalid or zero-area polygon"
        elif geometry.interiors:
            reason = "holes are not supported in this workspace"
        elif not np.isfinite(np.asarray(geometry.exterior.coords)).all():
            reason = "non-finite coordinates"
        if reason:
            rejected.append(f"Feature {position}: {reason}")
            continue
        record = row.to_dict()
        record[frame.geometry.name] = force_2d(geometry)
        record["_index"] = position
        accepted.append(record)
    if not accepted:
        raise ValueError("No supported polygon features. " + "; ".join(rejected[:8]))
    result = gpd.GeoDataFrame(accepted, geometry=frame.geometry.name, crs=frame.crs)
    for i, a in enumerate(result.geometry):
        for j in range(i):
            if a.intersection(result.geometry.iloc[j]).area > TOL*TOL:
                raise ValueError(f"Input features {result.iloc[j]['_index']} and {result.iloc[i]['_index']} overlap. Resolve overlap before loading.")
    return LoadedData(result, rejected, str(source))


def load_shapefile(path=DEFAULT_DATA):
    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError(f"Shapefile not found: {path}")
    return prepare_frame(gpd.read_file(path), str(path))


def visibility_kernel(polygon):
    """Intersect the left half-planes of a counterclockwise simple polygon."""
    polygon = orient(polygon, 1)
    xmin, ymin, xmax, ymax = polygon.bounds
    margin = max(xmax-xmin, ymax-ymin, 1)*4
    points = list(box(xmin-margin, ymin-margin, xmax+margin, ymax+margin).exterior.coords)[:-1]
    ring = list(polygon.exterior.coords)
    for a, b in zip(ring, ring[1:]):
        def side(p):
            return (b[0]-a[0])*(p[1]-a[1])-(b[1]-a[1])*(p[0]-a[0])
        output = []
        for p, q in zip(points, points[1:]+points[:1]):
            sp, sq = side(p), side(q)
            if sp >= 0:
                output.append(p)
            if (sp >= 0) != (sq >= 0):
                t = sp/(sp-sq)
                output.append((p[0]+t*(q[0]-p[0]), p[1]+t*(q[1]-p[1])))
        points = output
        if len(points) < 3:
            return Polygon()
    return Polygon(points).intersection(polygon)


def visible_point(polygon):
    kernel = visibility_kernel(polygon)
    if kernel.is_empty or kernel.area <= TOL*TOL:
        raise ValueError("This polygon has no interior visibility kernel; single-apex collapse is unsupported.")
    point = kernel.centroid
    if not polygon.contains(point) or any(not polygon.buffer(TOL).covers(LineString([p, point.coords[0]])) for p in polygon.exterior.coords[:-1]):
        raise ValueError("No verified point visible from every boundary vertex.")
    return point


def _valid_ridge(source, line):
    from transition_targets import _end_extensions
    if line.is_empty or line.length <= TOL or not line.is_simple or line.is_ring or not source.contains(line) or source.boundary.distance(line) <= TOL:
        return False
    try:
        ends = _end_extensions(source, line)
        pieces = list(polygonize(unary_union([source.boundary, LineString([ends[0], *line.coords, ends[1]])])))
        if len(pieces) != 2 or not all(p.area > TOL*TOL for p in pieces):
            return False
        # Reject targets whose end branches survive only as zero-volume flaps.
        from generalization_model import _collapse_patches
        _collapse_patches(-1,source,line,0.,1.)
        return True
    except (ValueError, ZeroDivisionError, np.linalg.LinAlgError):
        return False


def skeleton_ridge(source):
    """Longest valid skeleton path, then a kernel-axis fallback; never branches."""
    from py_straight_skeleton import compute_skeleton
    source = orient(source, 1)
    candidates = []
    try:
        skeleton = compute_skeleton(list(source.exterior.coords)[:-1], [])
        nodes, graph = {}, {}
        for a, b in skeleton.arc_iterator():
            pa, pb = (a.position.x, a.position.y), (b.position.x, b.position.y)
            nodes[pa], nodes[pb] = a, b
            graph.setdefault(pa, []).append(pb)
            graph.setdefault(pb, []).append(pa)
        # A polygon skeleton is a tree. Enumerate unique endpoint paths.
        ends = [p for p in graph if len(graph[p]) == 1]
        for i, start in enumerate(ends):
            for end in ends[i+1:]:
                stack = [(start, [start])]
                while stack:
                    p, path = stack.pop()
                    if p == end:
                        line = LineString(path)
                        # Remove boundary leaves, retaining almost the full path.
                        from shapely.ops import substring
                        trim = max(TOL*20, line.length*.02)
                        if line.length > 2*trim:
                            candidates.append(substring(line, trim, line.length-trim))
                        break
                    stack.extend((q, path+[q]) for q in graph[p] if q not in path)
    except Exception:
        # Fallback below is geometric, and is explicitly identified in the UI.
        pass
    for line in sorted(candidates, key=lambda g: -g.length):
        if _valid_ridge(source, line):
            return line, "straight-skeleton path"
    kernel = visibility_kernel(source)
    if not kernel.is_empty and kernel.area > TOL*TOL:
        center = kernel.centroid
        coords = list(source.minimum_rotated_rectangle.exterior.coords)
        a, b = max(zip(coords, coords[1:]), key=lambda ab: Point(ab[0]).distance(Point(ab[1])))
        dx, dy = b[0]-a[0], b[1]-a[1]
        scale = max(source.length, 1)/hypot(dx, dy)
        axis = LineString([(center.x-dx*scale, center.y-dy*scale), (center.x+dx*scale, center.y+dy*scale)])
        chord = axis.intersection(kernel)
        if chord.geom_type == "LineString" and chord.length > TOL*40:
            from shapely.ops import substring
            line = substring(chord, chord.length*.05, chord.length*.95)
            if _valid_ridge(source, line):
                return line, "visibility-kernel principal-axis fallback"
    raise ValueError("No valid simple interior ridge was found. Branched skeleton collapse is unsupported.")


def polygon_target(source, operation, tolerance=1., iterations=1, ratio=.25):
    if operation == "simplification":
        if not isfinite(tolerance) or tolerance <= 0:
            raise ValueError("Simplification tolerance must be positive and finite.")
        target = source.simplify(tolerance, preserve_topology=True)
    elif operation == "smoothing":
        if not isinstance(iterations, int) or not 1 <= iterations <= 10 or not isfinite(ratio) or not 0 < ratio < .5:
            raise ValueError("Smoothing needs 1–10 iterations and a ratio strictly between 0 and 0.5.")
        points = np.asarray(source.exterior.coords[:-1])[:, :2]
        if len(points)*2**iterations > 20000:
            raise ValueError("Smoothing would exceed 20,000 vertices. Reduce iterations.")
        for _ in range(iterations):
            following = np.roll(points, -1, axis=0)
            points = np.stack(((1-ratio)*points+ratio*following, ratio*points+(1-ratio)*following), axis=1).reshape(-1, 2)
        target = Polygon(points)
    else:
        raise ValueError("Unknown polygon operation.")
    if target.geom_type != "Polygon" or target.is_empty or not target.is_valid or target.interiors or target.area <= TOL*TOL:
        raise ValueError("The generated target is not a supported simple polygon.")
    return orient(target, 1)


def parse_osm(payload):
    """Assemble complete way rings and multipolygon members from Overpass JSON."""
    elements = payload.get("elements", [])
    nodes = {e["id"]: (e["lon"], e["lat"]) for e in elements if e["type"] == "node"}
    ways = {e["id"]: e for e in elements if e["type"] == "way"}
    records, used, errors = [], set(), []
    for relation in (e for e in elements if e["type"] == "relation" and "building" in e.get("tags", {})):
        outer, inner = [], []
        try:
            member_ids = []
            for member in relation.get("members", []):
                if member["type"] != "way":
                    continue
                way = ways[member["ref"]]
                line = LineString([nodes[n] for n in way["nodes"]])
                (inner if member.get("role") == "inner" else outer).append(line)
                member_ids.append(member["ref"])
            outside = unary_union(list(polygonize(unary_union(outer))))
            inside = unary_union(list(polygonize(unary_union(inner))))
            geometry = outside.difference(inside)
            if geometry.is_empty:
                raise ValueError("unclosed outer ring")
            records.append(dict(osm_id=f"relation/{relation['id']}", **{k:v for k,v in relation.get('tags', {}).items() if k != 'geometry' and k != 'osm_id'}, geometry=geometry))
            used.update(member_ids)
        except (KeyError, ValueError) as exc:
            errors.append(f"Relation {relation['id']}: {exc}")
    for ident, way in ways.items():
        if ident in used or "building" not in way.get("tags", {}):
            continue
        try:
            ring = [nodes[n] for n in way["nodes"]]
            if len(ring) < 4 or ring[0] != ring[-1]:
                raise ValueError("not a closed building ring")
            records.append(dict(osm_id=f"way/{ident}", **{k:v for k,v in way.get('tags', {}).items() if k not in ('geometry', 'osm_id')}, geometry=Polygon(ring)))
        except (KeyError, ValueError) as exc:
            errors.append(f"Way {ident}: {exc}")
    if not records:
        raise ValueError("No complete building polygons returned. " + "; ".join(errors[:5]))
    return gpd.GeoDataFrame(records, geometry="geometry", crs="EPSG:4326"), errors


def download_osm(west, south, east, north, endpoint="https://overpass-api.de/api/interpreter", max_bytes=50*1024**2):
    bounds = list(map(float, (west, south, east, north)))
    west, south, east, north = bounds
    if not all(map(isfinite, bounds)) or not -180 <= west < east <= 180 or not -90 <= south < north <= 90:
        raise ValueError("Bounding box must be west < east, south < north, in longitude/latitude degrees.")
    query = f'[out:json][timeout:60];(way["building"]({south},{west},{north},{east});relation["building"]["type"="multipolygon"]({south},{west},{north},{east}););out body;>;out skel qt;'
    request = urllib.request.Request(endpoint, data=urllib.parse.urlencode({"data": query}).encode(), headers={"User-Agent": "CodexDHE-Generalization/1.0 (interactive research tool)"})
    try:
        with urllib.request.urlopen(request, timeout=90) as response:
            payload = response.read(max_bytes+1)
    except (urllib.error.URLError,TimeoutError) as exc:
        raise ValueError(f"OSM download failed: {exc}. Retry later or use a local Shapefile.") from exc
    if len(payload) > max_bytes:
        raise ValueError("OSM response exceeds 50 MiB. Use a smaller bounding box.")
    try:
        payload = json.loads(payload)
    except (ValueError,UnicodeError) as exc:
        raise ValueError("Overpass returned an invalid JSON response. Retry later.") from exc
    if payload.get("remark"):
        raise ValueError("Overpass reported: " + payload["remark"])
    frame, errors = parse_osm(payload)
    data = prepare_frame(frame, f"OpenStreetMap buildings {bounds}; © OpenStreetMap contributors, ODbL")
    data.rejected[:0] = errors
    return data
