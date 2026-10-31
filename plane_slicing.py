"""Cross-sections of polygon-faced DHE shells in an arbitrary orthonormal plane."""

from dataclasses import dataclass
from collections import defaultdict
from itertools import product
from math import isfinite

import numpy as np
from shapely.geometry import LineString, Point, Polygon
from shapely.ops import polygonize, unary_union

import DHECore as dhe

EPSILON = 1e-8


@dataclass(frozen=True)
class SlicePlane:
    origin: tuple[float, float, float]
    normal: tuple[float, float, float]
    u: tuple[float, float, float]
    v: tuple[float, float, float]

    def __post_init__(self):
        for name in ("origin", "normal", "u", "v"):
            values = np.asarray(getattr(self, name), dtype=float)
            if values.shape != (3,) or not np.isfinite(values).all():
                raise ValueError("Plane coordinates must be finite 3D vectors.")
            object.__setattr__(self, name, tuple(float(x) for x in values))
        basis = np.array([self.u, self.v, self.normal])
        if not np.allclose(basis @ basis.T, np.eye(3), atol=1e-10, rtol=0):
            raise ValueError("The plane basis must be orthonormal.")
        if not np.allclose(np.cross(self.u, self.v), self.normal, atol=1e-10):
            raise ValueError("The plane basis must be right-handed.")

    @classmethod
    def horizontal(cls, z):
        if not isfinite(z):
            raise ValueError("Plane height must be finite.")
        return cls((0, 0, float(z)), (0, 0, 1), (1, 0, 0), (0, 1, 0))

    @classmethod
    def from_angles(cls, center, tilt=45.0, azimuth=0.0, offset=0.0):
        """Tilt from horizontal; normal azimuth clockwise from north (+Y)."""
        if not all(isfinite(x) for x in (tilt, azimuth, offset)) or not 0 <= tilt <= 90:
            raise ValueError("Plane angles and offset must be finite; tilt must be 0–90 degrees.")
        theta, phi = np.radians([tilt, azimuth])
        normal = np.array([np.sin(theta)*np.sin(phi),
                           np.sin(theta)*np.cos(phi), np.cos(theta)])
        normal[np.abs(normal) < 1e-15] = 0
        normal /= np.linalg.norm(normal)
        u = np.array([-np.cos(phi), np.sin(phi), 0.])
        v = np.cross(normal, u)
        origin = np.asarray(center, dtype=float) + offset*normal
        return cls(tuple(origin), tuple(normal), tuple(u), tuple(v))

    def project(self, xyz):
        points = np.asarray(xyz, dtype=float) - self.origin
        return np.stack((points @ self.u, points @ self.v), axis=-1)

    def lift(self, xy):
        xy = np.asarray(xy, dtype=float)
        return (np.asarray(self.origin) + xy[..., 0, None]*np.asarray(self.u)
                + xy[..., 1, None]*np.asarray(self.v))

    def signed_distance(self, xyz):
        return (np.asarray(xyz, dtype=float) - self.origin) @ self.normal


@dataclass
class _Face:
    points: np.ndarray
    normal: np.ndarray
    u: np.ndarray
    v: np.ndarray
    polygon: Polygon


def _face_frame(points):
    points = np.asarray(points, dtype=float)
    relative = points - points[0]
    normal = np.sum(np.cross(relative, np.roll(relative, -1, axis=0)), axis=0)
    length = np.linalg.norm(normal)
    if length <= EPSILON**2:
        raise ValueError("A slicing face has no valid planar area.")
    normal /= length
    u = next(p.copy() for p in relative[1:] if np.linalg.norm(p) > EPSILON)
    u /= np.linalg.norm(u)
    v = np.cross(normal, u)
    if np.max(np.abs(relative @ normal)) > EPSILON*10:
        raise ValueError("Slicing requires planar polygon faces.")
    return _Face(points, normal, u, v,
                 Polygon(np.column_stack((relative @ u, relative @ v))).buffer(EPSILON))


def _ring_key(ring):
    points = list(ring.coords)
    return frozenset(frozenset((a, b)) for a, b in zip(points, points[1:]))


def _regions_from_lines(lines, coplanar=()):
    """Polygonize atomic regions and retain odd ring nesting, including holes."""
    pieces = list(polygonize(unary_union(lines)))
    rings = {}
    for polygon in pieces:
        for ring in (polygon.exterior, *polygon.interiors):
            rings.setdefault(_ring_key(ring), Polygon(ring))
    filled = []
    for polygon in pieces:
        point = polygon.representative_point()
        if sum(ring.covers(point) for ring in rings.values()) % 2:
            filled.append(polygon)
    result = unary_union([*filled, *coplanar])
    if result.is_empty:
        return Polygon()
    if result.geom_type not in ("Polygon", "MultiPolygon") or not result.is_valid:
        raise ValueError("A plane section could not be assembled into valid polygons.")
    return result


class PlaneSlicer:
    """Compile face frames once for live control updates on a fixed DHE model."""

    def __init__(self, model):
        records=[]
        for cell in model.all_cells:
            for seed in dhe.get_UniqueFace_in_cell(cell.seed):
                records.append((cell.logical_feature_id,
                    [(e.V.x,e.V.y,e.V.z) for e in dhe.face_edges(seed)]))
        self._compile(records)

    @classmethod
    def from_face_records(cls, records):
        """Slice immutable realized boundaries without allocating/restoring DHE.

        records is an iterable of (owner, oriented XYZ ring) pairs, normally
        captured from generalization history's validated face records.
        """
        result=cls.__new__(cls)
        result._compile(records)
        return result

    def _compile(self, records):
        self.faces = {}
        face_owners = defaultdict(set)
        vertices = set()
        for owner, points in records:
            points=[tuple(p) for p in points]
            vertices.update(points)
            self.faces.setdefault(str(owner), []).append(_face_frame(points))
            face_owners[tuple(sorted(points))].add(str(owner))
        self.vertices = np.array(sorted(vertices), dtype=float)
        if len(self.vertices) == 0:
            raise ValueError("Cannot slice an empty model.")
        self.minimum = self.vertices.min(axis=0)
        self.maximum = self.vertices.max(axis=0)
        self.center = (self.minimum + self.maximum) / 2
        self.coplanar_owner = {key: min(owners) for key, owners in face_owners.items()
                               if len(owners) > 1}

    def offset_bounds(self, plane):
        values = (self.vertices - self.center) @ plane.normal
        return float(values.min()), float(values.max())

    def sections(self, plane, *, include_degenerate=False):
        intersections, projected = {}, {}

        def uv(point):
            key = tuple(point)
            if key not in projected:
                projected[key] = tuple(plane.project(point))
            return projected[key]

        results = {}
        for owner, faces in self.faces.items():
            lines, coplanar, contacts, excluded = [], [], [], []
            for face in faces:
                distances = plane.signed_distance(face.points)
                if distances.min() > EPSILON or distances.max() < -EPSILON:
                    continue
                if np.max(np.abs(distances)) <= EPSILON:
                    polygon = Polygon([uv(p) for p in face.points])
                    key = tuple(sorted(tuple(p) for p in face.points))
                    if self.coplanar_owner.get(key, owner) != owner:
                        excluded.append(polygon)
                    else:
                        coplanar.append(polygon)
                    continue
                points = {}
                for a, b in zip(face.points, np.roll(face.points, -1, axis=0)):
                    da, db = plane.signed_distance([a, b])
                    if abs(da) <= EPSILON:
                        points[uv(a)] = a
                    if abs(db) <= EPSILON:
                        points[uv(b)] = b
                    if da*db < 0 and min(abs(da), abs(db)) > EPSILON:
                        key = tuple(sorted((tuple(a), tuple(b))))
                        if key not in intersections:
                            first, second = map(np.array, key)
                            d_first, d_second = plane.signed_distance([first, second])
                            intersections[key] = first + (second-first)*d_first/(d_first-d_second)
                        point = intersections[key]
                        points[uv(point)] = point
                if include_degenerate:
                    contacts.extend(Point(point) for point in points)
                if len(points) < 2:
                    continue
                direction = np.cross(face.normal, plane.normal)
                if np.linalg.norm(direction) <= EPSILON:
                    continue
                ordered = sorted(points.values(), key=lambda p: p @ direction)
                # Adjacent candidates, rather than one first-to-last segment,
                # preserve multiple entries/exits through a concave polygon.
                for a, b in zip(ordered, ordered[1:]):
                    midpoint = (a+b)/2 - face.points[0]
                    if not face.polygon.covers(Point(midpoint @ face.u, midpoint @ face.v)):
                        continue
                    first, second = uv(a), uv(b)
                    if np.linalg.norm(np.subtract(first, second)) > EPSILON:
                        lines.append(LineString([first, second]))
            geometry = _regions_from_lines(lines, coplanar)
            if excluded:
                # A plane on an inter-cell face intersects both closed solids.
                # Assign that boundary area once, to the lowest feature ID.
                geometry = geometry.difference(unary_union(excluded))
            if not geometry.is_empty and geometry.area > EPSILON**2:
                results[owner] = geometry
            elif include_degenerate:
                contact = unary_union([*lines, *contacts])
                if not contact.is_empty:
                    results[owner] = contact
        return results

    def plane_outline(self, plane):
        """Clip the visible plane to a slightly padded model bounding box."""
        padding = np.maximum((self.maximum-self.minimum)*0.05, 0.1)
        bounds = list(zip(self.minimum-padding, self.maximum+padding))
        corners = list(product(*bounds))
        points = {}
        for i, a in enumerate(corners):
            for b in corners[i+1:]:
                if sum(x != y for x, y in zip(a, b)) != 1:
                    continue
                first, second = np.array(a), np.array(b)
                da, db = plane.signed_distance([first, second])
                if abs(da) <= EPSILON:
                    points[tuple(first)] = first
                if abs(db) <= EPSILON:
                    points[tuple(second)] = second
                if da*db < 0:
                    point = first + (second-first)*da/(da-db)
                    points[tuple(np.round(point, 10))] = point
        if len(points) < 3:
            return np.empty((0, 3))
        xyz = np.array(list(points.values()))
        xy = plane.project(xyz)
        middle = xy.mean(axis=0)
        order = np.argsort(np.arctan2(xy[:, 1]-middle[1], xy[:, 0]-middle[0]))
        return xyz[order]


def slice_model_plane(model, plane, *, include_degenerate=False):
    """Return local feature sections, optionally including point/line contacts."""
    return PlaneSlicer(model).sections(plane, include_degenerate=include_degenerate)
