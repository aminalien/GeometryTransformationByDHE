# Polygon aggregation preview and DHE transitions

Run `main.py` in PyCharm, or:

```powershell
.\.venv\Scripts\python.exe main.py
```

The default scene previews aggregation of features **8, 9, 17, 18, 21, 1123**.
Use `--scene transition` for the existing 3D polygon, point and polyline workflow.

The transition scene defaults use features **3000–3004**, source **3000**, and **polyline 3001** from
`LineGenerlized.shp`. Source 3000 retains its footprint through Z=5, then
transforms to the target at Z=10. The model contains **five merged DHE cells**.

The shapefiles are read only. Geometry is projected to **EPSG:32633**, shifted
by a common XY origin, and measured in metres.

## Aggregation geometry preview

The preview calculates the source union `UP`, its convex hull `CH`, and the
transition regions `CH − UP`. For the supplied six features, `UP` has four
components; the gap has two regions, including a region with two holes.
The source area is **639.229820 m²**, the gap area **567.452314 m²**, and the
convex-hull area **1206.682134 m²**.

Source boundaries expand at equal normal speed while the hull stays fixed.
The straight skeleton is calculated with `py_straight_skeleton==0.1.0` inside
a distant temporary frame, using the source union components as holes.
Bounded holes inside the union are handled as separate skeleton domains.
This follows the [exterior-skeleton frame method](https://doc.cgal.org/latest/Straight_skeleton_2/index.html).
Faces inherit the ID of their originating source edge; existing shared source
boundaries do not generate fronts. The result is clipped to `CH`.

The calculation rejects frame influence inside the hull and requires stable
source ownership when the frame margin doubles. It verifies source preservation,
zero area overlaps, and complete hull coverage. Failed skeleton calculations
report an error rather than using an approximate substitute.

Three aligned XY panels show:

1. Colored source footprints, the convex hull, and numbered gap regions.
2. Skeleton trajectories, red meeting targets, and purple hull targets.
3. Six colored top partitions, with dotted original source outlines.

**Merged outline** switches the third panel to one convex-hull polygon.
This is a geometry preview: it neither allocates DHE cells nor creates transition
triangles. The subsequent 3D stage will produce one aggregate DHE cell and retain
the original source IDs as provenance.

Only the selected IDs participate. Unselected feature **2001** intersects the
hull by approximately **4.99 m²** and is not treated as an obstacle.

```powershell
# Default aggregation preview
.\.venv\Scripts\python.exe main.py

# Validate and export without opening a window
.\.venv\Scripts\python.exe main.py --no-show --preview artifacts/aggregation-preview.png

# Preview another selection
.\.venv\Scripts\python.exe main.py --scene aggregation-preview --ids 8 17
```

## Target geometry

The **Target geometry** dropdown provides:

- **Polyline:** all four vertices of `osm_id=3001` in `LineSHP`.
- **Point:** the interior centroid of source 3000.
- **Polygon:** the existing target polygon `osm_id=3006`.

Switching rebuilds the shared cells in the same window. Camera orientation,
plane orientation/angles, step, clamped position, and 3D slicing visibility are
preserved. Failed reconstruction restores the previous DHE state and view,
and displays the error inline.

Point mode builds an inclined fan to an apex. Polyline mode extends the first
and last segment directions to the source boundary and constructs an inclined
boundary-chain strip on each side. The full line is retained without clipping,
buffering, or simplification. Triangle ownership follows existing source-wall
neighbours; separator sheets preserve neighbour relationships.

Each feature has a polygon bottom at Z=0. Neighbours retain polygon tops.
Source 3000 ends in a polygon, point, or ridge according to its target.
Point and line targets create **no zero-area top face**. All modes omit cap
triangulation diagonals and internal horizontal Z=5 faces. Inclined shared
transition triangles remain.

The surface is continuous and piecewise planar; slopes may change across edges.

## Views, colours and navigation

The window contains the **3D model**, **XY transition projection**, and a
separate **2D cross-section** panel.

- Each feature has a stable colour for its faces and section fills.
- The current half-edge is red. Its face uses the current cell colour at
  **50% opacity (alpha 0.50)**. Its independent fill remains visible even when
  cell shading is cleared. The background fill for that face is omitted to
  avoid duplicate coplanar layers.
- The projection shows the current edge in red and its companion face edges in
  cyan. Vertical edges project to points. Companion edges are not highlighted in 3D.
- Navigating to exterior dual vertex **VINF** colours the exterior shell purple.
- Click a projected transition triangle or a 3D edge, or enter an **Edge ID**.
- Drag 3D to rotate. `f`: next face edge; `s`: symmetric edge;
  `a`: adjacent cell; `v`: next vertex edge.
- `c`: shade current feature; `x`: clear cell shading while retaining the current
  face fill; `l`: clear the current face fill until the next half-edge selection
  or navigation; `q` / Escape: close.
  Checkboxes display dual vertices and half-edges.

Removed intermediate faces and cells are excluded from live navigation.

## Slicing controls

**3D slicing starts hidden.** The separate 2D section remains live.
Using the position slider, wheel, arrows, Lower/Raise, orientation, or angle
controls automatically enables **Show slicing in 3D**. Uncheck that bottom
checkbox to remove the plane and all 3D section contours while keeping 2D
sections and values active. Step edits and target switches preserve visibility.

| Plane orientation | Position slider | Angle controls |
| --- | --- | --- |
| Horizontal | Absolute Z within model elevations | None |
| Vertical | Signed offset from model bounding-box centre | Azimuth |
| Inclined | Signed offset from model bounding-box centre | Tilt and azimuth |

Normal azimuth is clockwise from north (+Y): **0° north, 90° east**.
Tilt is measured from horizontal: **0° horizontal, 90° vertical**.
Defaults are Z=7.5, azimuth 0°, inclined tilt 45°, and offset 0.
Angle sliders use 1° increments; offsets follow the current normal's bounds.

Move the plane using the position slider, mouse wheel over any view or the
slider, **↑/↓**, or **Lower/Raise**. All methods use the editable **Step (m)**
field. Press Enter or leave the field to commit. The default is **0.1 m**;
**0.01, 0.5, and 1** are also supported. Horizontal mode changes Z; other modes
change offset. Slider snapping uses the same increment, anchored at the bottom
elevation for Z and at zero for offset, with clamped endpoints.

Invalid steps retain the previous valid value and show an inline explanation.
Each orientation remembers its position. Plane movement does not zoom or move
the camera.

Horizontal sections use local XY coordinates; vertical and inclined sections
use the plane's orthonormal local `u,v` frame in metres. Polygon sections are
filled; point/line contacts are drawn in feature colours. At Z=10, source 3000
is shown as the exact point or full polyline in collapse modes. Status separates
positive-area sections from zero-area contacts.

Concave sections, disconnected components, holes, coplanar cuts and empty
results are supported. A plane exactly on an inter-cell face assigns that
boundary area to the lowest feature ID, preventing double-counting.

## Topology and validation

All five features remain closed genus-zero DHE solids with the original eight
logical neighbour pairs. For the supplied data and default elevations:

| Target | Inclined source triangles | Shared face pairs | Source volume |
| --- | ---: | ---: | ---: |
| Point | 8 | 28 | 1354.560343 m³ |
| Polyline | 16 | 36 | 1508.758645 m³ |
| Polygon | 24 | 44 | 1938.807230 m³ |

Horizontal and oblique cuts partition the full domain without gaps or overlaps.
Point-target source areas follow the analytical quadratic shrinkage toward the
apex. Neighbours fill the remaining top footprint as the source collapses.

Validation checks pointer identities, face cycles, reciprocal interfaces, Euler
characteristics, caps, positive volumes, neighbour preservation, terminal
contacts and slice coverage. Tests also cover colours/transparency, hidden
startup slicing, checkbox behaviour, all step controls, target switching,
failure recovery, settings preservation and callback cleanup.

## Startup options and earlier stages

```powershell
# Existing 3D transition scene
.\.venv\Scripts\python.exe main.py --scene transition --target-kind polyline --line-id 3001

# Other target types
.\.venv\Scripts\python.exe main.py --scene transition --target-kind point
.\.venv\Scripts\python.exe main.py --scene transition --target-kind polygon --target-id 3006

# Custom elevations
.\.venv\Scripts\python.exe main.py --scene transition --z-down 0 --height 5 --target-z 10

# Earlier polygon overlay/triangulation stages
.\.venv\Scripts\python.exe main.py --scene transition --target-kind polygon --no-transition
.\.venv\Scripts\python.exe main.py --scene transition --target-kind polygon --no-triangulation

# Ordinary extrusion
.\.venv\Scripts\python.exe main.py --scene transition --ids 2001 2002 --height 10 --no-overlay

# Save a preview and run checks
.\.venv\Scripts\python.exe main.py --scene transition --no-show --preview artifacts/polyline-collapse.png
.\.venv\Scripts\python.exe -m unittest -v
```

The path is configured by `LineSHP` or `--line-shp`; `--line-id` selects its
`osm_id`. Transition elevations must satisfy `z-down < height < target-z`.
The overlay-only stage flags require polygon mode. `--no-overlay` remains
ordinary extrusion regardless of startup target kind.

## Callable API

- `construct_aggregation_preview(path, ids)` loads projected sources and returns
  an `AggregationPreview` without modifying global DHE state.
- `build_aggregation_preview(sources, origin=(0, 0), tolerance=1e-6)` accepts a
  source-ID-to-local-Polygon mapping. The result exposes source geometries,
  union, hull, gap regions, labeled growth faces and skeleton arcs (with event
  times), per-source targets, meeting/hull target lines, and top partitions.
  `validate_aggregation(preview)` returns area coverage metrics.
- `main()` returns an `AggregationPreview` for the default scene, or an
  `ExtrusionModel` for `--scene transition`. `--ids` defaults depend on the scene.
- `construct_model(args, target_kind=...)` builds and validates a model without a viewer.
- `build_model()`, `overlay_top_faces()`, and `triangulate_active_regions()`
  retain the earlier polygon construction workflow.
- `TargetDefinition(kind, identifier, geometry)` holds a local Point, LineString,
  or Polygon target; `load_line_target()` loads and reprojects a line.
- `build_transition_layer(model, target_z=10, target=definition,
  source_id="3000", source_z=5)` supports collapsed targets from unsplit base roofs.
  Polygon mode uses the existing roof overlay.
- `model.cells` and `model.all_cells` refer to the same final cells.
  `active_edges()` excludes retired records. The legacy `include_transition`
  argument is accepted; merged cells span both height intervals.
- `model.transition.cell` and `.source_cell` identify merged feature 3000.
  The result exposes final facets, connecting facets, bottom/top caps, shared
  interfaces, target kind/definition and geometry-only projection regions.
  `.horizontal_section(z)` returns the exact target geometry at Z=10.
- `slice_model(model, z)` returns positive-area horizontal sections.
- `SlicePlane.horizontal(z)` and
  `SlicePlane.from_angles(center, tilt, azimuth, offset)` define a plane.
  `plane.project(xyz)` and `plane.lift(uv)` convert frames.
- `slice_model_plane(model, plane, include_degenerate=False)` returns local
  section geometries by feature ID. Setting `include_degenerate=True` includes
  actual point/line contacts. `PlaneSlicer(model).sections(plane, ...)` caches
  fixed face geometry for repeated cuts.
- `ViewerSession(model, builder)` owns the live viewer and transactional
  `.switch_target(kind)`; its current model is available through `.model`.

Polygon overlay metadata is retained as construction provenance. Final
navigation uses live transition facets. Earlier-stage validators apply before
final construction; `validate_transition_layer()` checks the final solids.

## Supported targets and dependencies

Footprints must be valid single-ring polygons with unique IDs. Polygon overlays
require boundary-crossing arcs; interior target loops and coincident segments
are unsupported. Point targets must be interior and visible from the full
source boundary. Polyline targets must be simple, open, single-part lines
strictly inside the source, with nonzero segments and valid end extensions.
Source transition edges require constructed neighbours. Neighbour top footprints
must remain single-ring polygons with original adjacency. Unsupported geometry
fails preflight before DHE construction.

Install `requirements.txt` in the configured environment. Constrained polygon
triangulation requires **Shapely >=2.1** and **GEOS >=3.10**. Aggregation adds
**py_straight_skeleton 0.1.0**, which requires **Matplotlib >=3.10**. No external
`Slicing.py` is required.

`DHECore.py` is a construction-focused extraction of
[aminalien/DHE/DHECore.py](https://github.com/aminalien/DHE/blob/main/DHECore.py),
source blob `a4a7a56e7881611c39b50973c40e571dab84a011`.
The interactive viewer originates from the same repository, with local
transition construction, materials, projection selection and slicing controls.
