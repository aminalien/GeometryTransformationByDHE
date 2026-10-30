# Transition conformity and unchanged cell lifting

This change addresses shared-coordinate construction and final shell validation across the polygon, point, polyline and aggregation workflows. It does not substitute a different map-generalization method or integrate iterative Delaunay propagation.

## Root cause matrix

| Failure class | Affected paths | Location and evidence | Correction or diagnostic |
|---|---|---|---|
| Inconsistent junction XYZ | Reproduced in workspace P2P simplification; relevant to all shared layer construction | `generalization_model.py`: input precision reduction, `Patch`, `_polygon_patches`, `_layer_faces`, `_node_faces`. The baseline Feature 3 case has valid source/target polygons and six unmatched edges. Reconstructed target Z differs from its cap level. Decimal quantization and distance-based BOTH roles introduce inconsistent constrained positions. | Preserve unrounded coordinates through the planning path; classify actual boundary incidence; retain explicit endpoint and constant-edge levels; cache evaluations and use the same arithmetic-equivalent coordinates. No modelling-tolerance increase. |
| Non-conforming subdivision | Shared walls, source/target contacts and batch interfaces | `_conform_footprints`, `_layer_faces`, `_node_faces`. An edge endpoint is missing from an incident ring, or subdivision is evaluated independently. | Insert nodes on their supporting edges using arithmetic error bounds. Conform both sides before pairing. Different level roles remain distinct. |
| Lost input-interface provenance | Neighbouring polygons in every workspace case | `_conform_input_interfaces`: a real world-coordinate intersection can cease to be exactly collinear after independent origin subtraction. | Insert shared intersection endpoints on both input rings before translation. The relation comes from an actual boundary intersection, not proximity. |
| Incorrect ownership of a tiny edge | Reproduced during workspace polyline collapse | `_recipient`: comparing uncovered edge length to an absolute tolerance previously accepted an arbitrarily distant neighbour when the entire edge was shorter than that tolerance. | Require actual boundary containment or membership on a corresponding supporting segment. Do not assign an owner merely because the edge is small. |
| Missing or duplicated facets | Any shell planner | Common `check_oriented_shell`; controlled missing/duplicate-face tests. | Report incidence, oriented endpoints, face indices and parts. Do not infer that triangulation is impossible from a missing edge partner. |
| Winding or ownership errors | Per-cell shells and inter-cell interfaces | `check_oriented_shell`, `check_interface_pairs`; reversed-face and wrong-owner tests. | Require opposite directed uses and distinct owners for a paired interface. Do not flip faces or join nonmatching faces automatically. |
| Excess edge/face incidence | All cases | Structured incidence diagnostics and duplicated-interface tests. | Reject more than two uses per cell edge or more than two cells per complete interface. |
| Degenerate, disconnected, pinched or intersecting geometry | All final boundaries; source/target restrictions remain case-specific | Connectivity and vertex-link checks; polygon/plane intersection checks; closed but intersecting synthetic shell. | Diagnose each separately. Check face intersections before allocating replacement DHE records. Retain valid vertical faces with zero XY projection; reject zero 3D area. |
| Coincident XY at different levels | Crossing/separator geometry, collapse ridges, skeleton growth | Coordinate registry and exact-level tests. | Preserve semantic levels. Neither XY coincidence nor numerical proximity authorizes merging source and target instances. |
| Terminal point/line contact | Point and polyline collapse | Existing final-contact validation, plus ridge admissibility tests. | Validate the positive-volume solid below termination and the actual point/line at its terminal section. Do not invent a polygon cap. The workspace ridge planner checks the same admissible moves for every Z interval. |
| Invalid updated neighbour footprint | Coupled operations, including smoothing and collapse | `preview`, neighbour-union stage. | Report the cell, geometry type, validity and component areas. A split or holed result is not silently repaired, and this error is not automatically labelled numerical. |
| Redundant unchanged intervals | Repeated workspace operations | Baseline isolated boxes grow from V/E/F=8/12/6 to 12/20/10 and then 16/28/14. | Raise a safe isolated candidate roof directly. Otherwise dissolve only adjacent coplanar vertical walls with identical interface ownership and remove unnecessary collinear nodes. Preserve earlier inclined faces and ownership partitions. |

Raw dataset-coordinate diagnostics are generated locally under ignored `artifacts/manifold/`; they are not part of the code commit. The audit records outcomes rather than claiming a single cause for every rejected transition.

## Implementation and operation order

`generalization_model.py` prepares candidate boundaries independently of the live DHE model. It records actual input-interface intersections before translating to local coordinates, then uses actual source/target incidence, shared evaluations of patch junctions, and exact required levels. Constant-level patch edges do not obtain Z from a barycentric reconstruction. Intermediate patch values keep the existing strip, fan, ridge or skeleton-growth assignments. Arithmetic identity bounds are componentwise, so a large transition Z cannot erase a small real XY gap.

Non-active regions use their rings directly. They do not enter constrained triangulation simply to create vertical continuation walls. A safe unchanged isolated prism raises its candidate roof vertices and extends its walls before DHE allocation. Adjacent coplanar vertical wall bands can be combined only when both their owner and their opposite owner agree. True corners in any incident face retain the corresponding node; this protects old inclined transitions and neighbour junctions.

The candidate shell is checked before and after wall coalescing. Topological checks cover oriented incidence, connectedness, vertex links and genus-zero support. Final geometric checks use bounding-box filtering and polygon/plane intersections to detect undeclared face intersections within and between cells. Legitimate paired faces, declared edges and vertices are allowed. These numerical checks use arithmetic error bounds; they are not an exact-arithmetic certificate for arbitrary geometry.

Intersection parameters at declared vertex/edge junctions are evaluated from those known constraints. This avoids interpreting a numerically clipped tangency as a new crossing. Contact along an explicitly shared edge is checked in its common one-dimensional parameter interval, rather than by expanding a geometric buffer.

Validated faces are realized with the existing sequence `MEVVFS`, `MEV`, `MEF`, followed by `Sew` within each cell and `JoinByFace` for matched interfaces between distinct cells. The standalone aggregate still constructs one aggregate cell; the change does not turn it into generic pairwise `JoinByFace` merging. `DHECore.py` is unchanged.

The legacy layer builder also snapshots its existing topology records, mutable containers, ledger contents, counters and model metadata. Allocation or postvalidation exceptions restore that state. Regression tests inject both failures for polygon, point and polyline cases; the existing aggregate allocation-rollback tests remain applicable.

The original paper P2P, collapse and aggregation paths retain their planners. They share the strengthened shell diagnostics and perform final geometric checks before replacement DHE allocation. Existing construction conventions, endpoint levels, cap semantics and skeleton arrival-time assignments are retained.

## Transition coordinates and history

`z_b`, `z_s`, `z_m` and `z_t` are representation-transition coordinates, not measured terrain elevations. A case uses only the levels it actually defines. Aggregation retains its existing growth interval ending at `z_m`; source, target and intermediate skeleton constraints are not replaced by generic iteration counts.

Raising every vertex of a previously transformed roof can stretch its old inclined facets. That would change earlier sections even though its current XY footprint is unchanged. This implementation preserves that history. It removes only unnecessary seams, keeping real creases between an earlier transition and a vertical continuation, as well as partitions where interface ownership changes.

Unchanged isolated and shared-wall boxes retain V/E/F=8/12/6 through repeated unrelated operations. An unchanged cell with an earlier inclined transition can legitimately retain more vertices/faces. The previous model and history snapshots are not mutated during planning or failed validation.

## Assessment of the supplied helper

The attachment `Delaunay triangulation and inclind transition faces.py` was inspected together with both Word explanations and their conceptual ATR figure.

- `DelaunayTriangulation(e)` ear-clips a single planar simple DHE face with `MEF` and performs Lawson flips with `KEF + MEF`. It mutates topology and does not directly support holes.
- `GetSourceVerticesByIteration` reads existing triangular connectivity and greedily assigns source-seeded levels. It does not flip edges, cannot handle three fixed source seeds in one triangle, and has no argument that pins every genuine target seed to the last level. A face already consisting of one triangle also needs special handling because the helper expects internal diagonals.
- `AssignZByIteration` uses `z_i = z_0 + (z_n-z_0)i/n` and mutates existing `Vertex.z`, including objects potentially shared by caps or neighbouring cells.

The idea can be useful for an ATR whose valid constrained boundary cannot be connected by the direct strip planner. Before adopting it, a separate solver would need genuine source/target endpoint constraints, safe level-specific vertex copies, hole-aware constraints, interface conformity, and embedded-shell validation. Avoiding equal-Z triangles alone does not prove these properties.

**No iterative-propagation fallback was added.** Feature 3's numerical seam is repaired independently of triangulation substitution. A remaining strip-planning failure is reported rather than changing source/target semantics automatically. The pre-existing workspace boundary-distance extension remains distinct from both the paper's direct strips and the proposed iterative scheme.

## Reproduction and result records

```powershell
.\.venv\Scripts\python.exe -m unittest test_transition_conformity -v
.\.venv\Scripts\python.exe -m unittest test_generalization -v
.\.venv\Scripts\python.exe -u audit_transition_manifolds.py --all-features
```

The audit loads the original workspace builder from Git commit `32eeaf4` in its own process, using the current diagnostic validator. It never replaces production files. It reports baseline/updated outcomes, exact-cycle hashes, V/E/F counts, ownership, volumes, terminal geometry types and previous-level section differences. Its six Feature 3 tolerances are 0.01, 0.1, 0.5, 1, 2 and 5 m. `transition_audit.md` and `transition_audit.json` list remaining rejections and their evidence.

Regression tests additionally cover shared unchanged cells, preserved inclined history, source/target instances at almost coincident Z, real small XY gaps, zero/unresolvable intervals, near-boundary junctions, four transformation cases, allocation rollback and native controls. Test results apply to the tested configurations; they do not establish blanket correctness for every possible transition.

### Verified regression results

With `DHE_GUI_TESTS=1`, `python -B -m unittest discover -q` passed 151 tests. A final focused run of `python -B -m unittest test_generalization test_transition_conformity test_generalization_ui -q` passed 37 tests, including the subsequently added horizontal-transition-facet rejection test. Both runs used the project's configured virtual environment. Native GUI controls, injected allocation/postvalidation failures, unchanged-cell counts and preserved historical sections are included in these checks.

## Suggested manuscript wording

“Conforming source–target preparation establishes shared boundary junctions, vertex roles and cell ownership before transition facets are realized. Each required terminal vertex is assigned its prescribed transition level; intermediate coordinates follow the case-specific strip, fan, ridge or skeleton-growth construction. Adjacent cells use common interface coordinates and opposite facet orientations. Non-active regions retain polygonal boundaries, and unchanged representations extend to the next level without redundant coplanar subdivisions, while earlier transitions and necessary ownership partitions are preserved.”

“Temporary planar triangulation is an implementation aid where used; it is distinct from the final inclined DHE facets. Final boundary validation checks oriented incidence, connectedness, vertex links, interface correspondence, geometry and relevant sections and volumes. Invalid or unresolved configurations are reported explicitly. Iterative triangulation-based propagation is a possible extension for demonstrated planning infeasibility and is not assumed to repair numerical conformity defects.”
