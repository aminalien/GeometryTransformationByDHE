# DHE Map Generalization

Run the existing `main.py` and click **Generalization…** in the paper viewer, or launch directly:

```powershell
.\.venv\Scripts\python.exe main.py --scene generalization
```

The generalization workspace runs in an independent process when launched from the paper viewer. This is intentional: `DHECore` keeps process-global topology registries. The paper builders and publication figure generator are unchanged.

## Data and selection

The default is `C:/University/PhD/Data/Kleczkow Updated/osm_data1.shp`. Browse for a `.shp` with its accompanying Shapefile files, or use **OSM bbox…** to download building footprints from Overpass. Enter west, south, east, north in longitude/latitude degrees. Full building rings are retrieved, including nodes outside the selection box; footprints are not clipped into artificial shapes. OSM data retain attribution and require an Internet connection. No additional Python dependencies are needed.

An existing projected CRS in metres is preserved. Geographic or other linear units are converted to estimated local UTM. Calculations use local XY coordinates; the original CRS and origin remain available on the session.

Feature labels are stable **one-based input positions**, not OSM IDs. Rejected records do not renumber other features. Select/deselect by clicking polygons, right-drag a rectangle to add features, or Shift + right-drag to remove features. The table supports multiple selection. Index entry accepts `1, 3-6, 10`. Original IDs are recognized from `osm_id`, `id`, `fid`, or `objectid` (case insensitive). **Attributes / lineage…** explains identities after merging or aggregation.

The loader reports unsupported records explicitly. Initial geometry supports valid, non-overlapping, hole-free Polygon features. Multipart features, holes, invalid geometry, and positive-area overlaps are not silently repaired. The default dataset has 32 supported polygons, with both shared boundaries and isolated buildings.

## Generalization operations

All operations start from the **actual current DHE top section**. Planning preserves unrounded coordinates and removes only arithmetic-equivalent collinear nodes. Source boundaries are conformed wherever neighbours start or stop along an edge. Shared identities are not created by globally rounding coordinates or increasing a modelling tolerance.

| Operation | Behaviour |
|---|---|
| Simplification | Topology-preserving Douglas–Peucker; tolerance in metres, default 1. |
| Smoothing | Closed-ring Chaikin corner cutting; default one iteration, ratio 0.25. Parameters are validated; vertex growth is bounded. |
| Collapse to point | Uses the centroid of the visibility kernel. The point must be interior and visible from every boundary vertex. Polygons with an empty interior kernel cannot use the single-apex method. |
| Collapse to polyline | Searches straight-skeleton paths for a simple, realizable interior ridge. Uses a principal-axis segment through the visibility kernel if necessary. The chosen method is reported. Branched targets and targets surviving only as zero-volume flaps are rejected. |
| Merging | Select touching polygons and choose a winner. Their exact union is the winner's target. The winner keeps its attributes; lineage records all sources. A disconnected or holed union is rejected. |
| Aggregation | Uses the existing source-driven straight-skeleton growth construction to reach the selected polygons' convex hull. Creates a new stable aggregate index. Growth completes at 80% of the new interval, followed by the hull cap. |

Simplification, smoothing, and collapse accept several sources in the same interval. Conflicting polygon targets are rejected as a complete batch. **Preview** constructs and validates a candidate DHE model, outlines its targets on the current map, and displays its candidate 3D cells. **Apply** accepts that preview; if none exists, it computes and validates one first. Changes to selection or parameters invalidate the preview. A failed operation leaves the current session unchanged.

The initial extrusion is Z=0–5. Each Apply advances **ΔZ=10** by default, or the positive increment entered by the user. An unchanged simple cell raises its roof and extends existing walls without adding redundant level rings or wall bands. Shared coplanar walls are combined only when interface ownership permits. Existing neighbours can receive vacated source regions or lose areas entered by a target. Empty space remains exterior; artificial background polygons are not created. Neighbour updates that produce empty, disconnected, holed, overlapping, or invalid footprints are rejected.

Point and line representations are terminal: they remain visible on later 2D maps while their solids end at the collapse height. They are not given a fabricated polygon cap or extruded into new volume.

## Continuous cells and history

The model retains the external boundary below each new interval and removes the previous horizontal roof. New upper faces are integrated into continuous closed cells; there is no hidden second cell per height interval. The updated map comes from the realized top section, not a cosmetic target overlay.

Merging and aggregation combine the selected histories into one continuous cell, removing their internal shared boundaries. Earlier identities and partitions remain inspectable by selecting the preceding history entry. Exact-union merging dissolves ownership boundaries; it does not invent an external growth surface where the occupied union is unchanged. Aggregation has a real growth surface in the hull-minus-union region.

**Undo** or the history selector restores an earlier DHE model. Applying after navigating backwards replaces the later history branch. History is in memory for this session; closing the window does not save a project file.

## DHE construction and methodological scope

The workspace reuses the paper's boundary-chain strips, collapse-strip helpers, and straight-skeleton aggregation geometry when their preconditions hold. It adds a conforming vertical-interval arrangement to support exterior space, multiple sources, and repeated levels. Genuine shared interfaces use `JoinByFace`; faces within each shell use `Sew`, following `MEVVFS`, `MEV`, and `MEF` face construction. No existing Euler operator was changed.

Temporary planar DHE triangulation is not a prerequisite in this workspace. Final transition facets are real DHE faces. Nested or complex polygon active regions that cannot use the paper's two-crossing strip are triangulated geometrically and lifted by

```
z(p) = z_source + (z_target - z_source) d_source(p) / (d_source(p) + d_target(p)),
```

where distances are to the source and target boundaries. Shared boundary contacts use the midpoint level in this extension. An interior midpoint-height vertex subdivides an otherwise flat active triangle. The UI reports when this additional construction is used; it is **not a claim that the original paper workflow implemented this formula**. Ridge strips are also extended to exclude zero-projection moves that would leave folded flaps instead of the requested final contact.

The integration builds checked, oriented shells before allocating replacement DHE records. Checks include edge pairing, vertex links, genus-zero topology, planarity, positive volume, reciprocal dual/primal links, actual source/target sections, actual collapsed contacts, and sampled intermediate overlap checks. These are numerical checks, not a general proof of self-intersection freedom for arbitrary inputs.

Complex cases can still fail the conforming-shell checks even when the input polygon itself is valid. Such cases are reported as unsupported by this implementation; no substitute geometry is accepted. In particular, branched skeletons, non-star-shaped point collapses, split/holed neighbour results, and incompatible batches require different selections, parameters, or further algorithm development.

The original workspace audit is retained in `artifacts/generalization/default_dataset_matrix.json`. Updated before/after results and all six Feature 3 tolerance cases are produced with `audit_transition_manifolds.py --all-features` under `artifacts/manifold/`. Rejections remain explicit and transactional; outcome counts describe the tested parameters, not a guarantee for other selections or later levels. See `TRANSITION_MANIFOLD_NOTES.md` for the root-cause matrix, the triangulation-helper assessment, and methodological wording.

## Viewing and exporting

The left map always shows the current top; the large 3D panel shows the actual DHE shell; the lower 2D panel shows the live section. The current half-edge is red and its face remains filled at 50%, including when cell filling is disabled. Face/edge buttons browse actual DHE records. **Open full DHE inspector** opens the existing navigation viewer; it closes when its model is replaced. **Last transition diagnostics…** shows the failed operation's case, parameters, cell, validation class and detailed evidence when available.

Plane controls support Horizontal (Z), Vertical (azimuth/offset), and Inclined (tilt/azimuth/offset). Enter a positive **Step size** to control slider resolution, mouse wheel over the slider or section, ↑/↓, and Lower/Raise consistently. The 3D plane is hidden initially; interacting with the slider/keys/buttons enables it. The checkbox can hide it again. Tilt is measured from horizontal. The **3D Z exaggeration** field changes only display aspect, not coordinates or calculations.

**Export current figure…** writes PNG, SVG, or PDF at a requested DPI. Pan/zoom and camera controls are available through Matplotlib. Exports and external datasets should remain outside Git commits.

Headless examples:

```powershell
.\.venv\Scripts\python.exe main.py --scene generalization --no-show
.\.venv\Scripts\python.exe main.py --scene generalization --no-show --generalization-operation smoothing --indices 1 4 --increment 10 --preview artifacts/generalization/smoothing.png
.\.venv\Scripts\python.exe main.py --scene generalization --no-show --generalization-operation merging --indices 2 5 --winner-index 2
.\.venv\Scripts\python.exe main.py --scene generalization --no-show --generalization-operation aggregation --indices 1 4
```

For OSM in headless mode, add `--osm-bbox WEST SOUTH EAST NORTH`. For another Shapefile, use `--input-shp PATH`. GUI operations and OSM downloads use the window controls; the operation-specific CLI flags require `--no-show`.

Programmatic sequences use `GeneralizationSession.preview(...)`, `apply(proposal)`, `undo()`, and `go(history_index)`. A proposal includes its validated model, target geometries, affected indices, and method notes. Applying a proposal from an earlier state is rejected.

Tests:

```powershell
.\.venv\Scripts\python.exe -m unittest test_generalization -v
$env:DHE_GUI_TESTS='1'
.\.venv\Scripts\python.exe -m unittest test_generalization_ui -v
```

The real-dataset test runs when the local default Shapefile exists; portable tests use synthetic geometries with the production builders. Live Overpass availability is separate from parser/geometry correctness.
