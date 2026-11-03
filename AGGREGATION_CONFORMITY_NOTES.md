# Aggregation timing and column conformity

## Reproduced defects

Both selections reproduce with the default dataset's original footprints, before any prior generalization. The supplied values are the workspace's one-based feature indices.

| Selection | First invalid construction | Evidence and correction |
|---|---|---|
| 17, 27, 30 | `aggregation_layer._plan_shell`, shared growth-vertex elevation | Three incident edge-distance estimates produce a Z spread of approximately 1.43e-6 for the default interval. Two source edges are nearly collinear. The existing skeleton arcs agree on the event time; use that shared provenance rather than independent per-face heights. |
| 1, 26, 29, 32 | `generalization_model._layer_faces`, column-edge pairing | A three-point NATR arrangement ring has area about 4.97e-14 and perpendicular width about 5.24e-15, below the local arithmetic correspondence bound. It traverses the same edge forwards and backwards. Omit the collapsed ring before creating a column; retain actual narrow regions. |
| 17, 5, 7, 10, 27, 30 | `_growth_times`, event time versus incident edge planes | The backend treats a 0.154506° turn as aligned and propagates along the incoming edge normal. The resulting edge-plane discrepancy is about 2.0e-5, larger than its base 1e-5 precision. Verify this approximation geometrically and propagate a local bound through the original event graph; retain the original event times and geometry. |

No source polygon, hull target, skeleton topology or DHE operator was changed. No global modelling tolerance was increased, and no invalid shell is accepted by bypassing its validator.

## Shared timing and Z assignment

The skeleton backend provides event-node times. `SkeletonArc.times` already stores their linear interpolation at clipped arc endpoints; arc component/node IDs and clipping fractions now preserve that provenance explicitly.

`_growth_times` resolves each shared growth vertex once. Source-boundary time is exactly zero. Skeleton endpoints use their recorded event/arc time; a point inserted along an arc uses its interpolated time. Only an interior point without arc provenance may use a mutually consistent source-edge estimate. Conflicting arc times are rejected, not averaged.

The common height field is

`z(v) = Zs + (Zm - Zs) * t(v) / tmax`,

where `tmax` is the maximum canonical time over the prepared nodes. Zero time maps exactly to Zs and maximum time exactly to Zm. The existing upper hull band continues from Zm to Zt. In the workspace, Zm remains `Zs + 0.8 * ΔZ`. These are transition coordinates, not terrain elevations.

The backend rounds event times and treats near-collinear edges numerically. Incident source-edge distances are checked against its documented `DISTANCE_EPSILON` and `TIME_EPSILON` (both 1e-5 in the installed version), plus only the locally verified approximation allowance described below. These bounds are distinct from shared-coordinate identity and shell-validation bounds. Unexplained disagreement, missing provenance or contradictory arc times raises a structured `skeleton_time_conflict` diagnostic.

### Verified near-collinear propagation

`skeleton_timing.py` receives the immutable, full event graph before hull clipping, including frame faces and original node/face IDs. The installed backend's alignment threshold is read from `TIGHT_COLLINEARITY_EPSILON_DEGS` (0.26° here). An arc seeds an allowance only when its two incident source-edge directions differ by at most this angle, the actual displacement is perpendicular to one edge within the local arithmetic bound, and the edge-plane difference agrees with that displacement. At a zero-time source corner, the incident edges must also meet at that original corner. Merely observing a large residual does not generate an allowance.

For normal travel λ and turn θ, the verified approximation contribution is

`δ = λ (1 − cos θ) = λ sin² θ / (1 + cos θ)`.

The second expression avoids cancellation at small angles. Contributions retain unique component/arc IDs and propagate forward in event time. Branch mergers union the IDs rather than counting the same contribution twice. Equal-time connected nodes share the contributions without merging their coordinates or topology. Clipped/inserted arc points interpolate the endpoint contributions using their original clipping fraction. When several arcs support a retained vertex, the smallest total allowance is used.

For a vertex v the comparison limit is `B(v) = max(DISTANCE_EPSILON, TIME_EPSILON) + arithmetic_bound(v) + Σ δᵢ(v)`. This is a bound on the independent edge-plane comparison, not a new timing assignment or a global snap tolerance. Recorded event times must still agree at the strict arithmetic bound; source-boundary times remain exactly zero. A retained original event must also have at least one incident edge-plane time agreeing within the base bound. Unexplained conflicts still fail. No vertex XY/Z, source edge, target hull, DHE operation, shell validator or backend constant is changed by this check.

For the six-feature example at Zs=15, Zm=23, Zt=25, the reported first discrepancy is explained by `λ ≈ 5.515870079`, `θ ≈ 0.154506°`, giving `δ ≈ 2.00553738e-5`. Downstream events inherit contributions, so testing only that first point would be insufficient. The final realized model has 351 faces. The actual intermediate surface still uses the original canonical event/arc time field and the height formula above.

Successful operation metadata contains the maximum edge-time discrepancy, backend precision constants and timing-source counts under `operation.diagnostics.skeleton_timing`, plus `explained_vertices`, `maximum_propagated_approximation_bound` and the contributing `approximation_sources` (angles, normal travel, δ, nodes, faces and owners). Each explained vertex records base/local/effective bounds and source IDs. Numerically collapsed column rings are recorded separately under `collapsed_arrangement_rings`. Remaining excess column incidence reports the edge, all incident columns and their owners as `transition_column_incidence`.

## Independent numerical checks

`AggregationLayer.horizontal_section` retains the ideal source-edge-distance wavefront reference, including its independently calculated maximum time. It is not silently redefined to match the new surface.

`AggregationLayer.event_time_section` independently clips immutable pre-allocation growth triangles against a requested Z. It uses the planned event-time field and does not read live DHE vertices. Actual DHE sections must match this planned section at the existing strict numerical thresholds. Differences from the ideal edge-plane reference are measured separately against the backend's precision; shell, volume, cap and interface checks are unchanged.

Consequently, previously valid irregular examples can have small intermediate-coordinate and volume changes. The paper aggregation fixture retains its 68 connecting facets and 121 total facets; its volume changes from approximately 8520.712540787 to 8520.712553838. This is a timing-field correction, not a change in source/target geometry or cell topology. The exact volume regression is updated for that field, while the old edge-plane reference remains available for comparison.

## Regression coverage

```powershell
.\.venv\Scripts\python.exe -B -m unittest test_skeleton_timing test_aggregation test_aggregation_layer test_aggregation_conformity -q
$env:DHE_GUI_TESTS='1'
.\.venv\Scripts\python.exe -B -m unittest test_generalization_visualization -q
```

The first two selections are tested at source levels 5 and 25 with increments 1, 10 and 20. The six-feature selection adds source level 15. These fixtures use the original XY footprints extruded to each source level; they do not claim to reconstruct an unknown earlier sequence of footprint edits. Checks cover source/hull preservation, monotonic intermediate sections, hull completion at Zm, positive volumes, historical sections and unchanged live state during preview. The six-feature test also removes timing graphs to verify strict rejection without provenance and deliberately corrupts edge-plane estimates to verify that the local allowance does not accept an unexplained error. Portable graph tests exercise branch mergers, equal-time components, reversed arcs, clipping interpolation and unverified-ray rejection.

Additional tests reject contradictory timing provenance, distinguish a collapsed ring from a real narrow region, retain preflight/allocation rollback checks, and exercise filled/wireframe figures, hidden/restored slice coordinates, zero/custom spacing and publication exports. Generated diagnostics and figures remain under ignored `artifacts/`.
