# Aggregation timing and column conformity

## Reproduced defects

Both selections reproduce with the default dataset's original footprints, before any prior generalization. The supplied values are the workspace's one-based feature indices.

| Selection | First invalid construction | Evidence and correction |
|---|---|---|
| 17, 27, 30 | `aggregation_layer._plan_shell`, shared growth-vertex elevation | Three incident edge-distance estimates produce a Z spread of approximately 1.43e-6 for the default interval. Two source edges are nearly collinear. The existing skeleton arcs agree on the event time; use that shared provenance rather than independent per-face heights. |
| 1, 26, 29, 32 | `generalization_model._layer_faces`, column-edge pairing | A three-point NATR arrangement ring has area about 4.97e-14 and perpendicular width about 5.24e-15, below the local arithmetic correspondence bound. It traverses the same edge forwards and backwards. Omit the collapsed ring before creating a column; retain actual narrow regions. |

No source polygon, hull target, skeleton topology or DHE operator was changed. No global modelling tolerance was increased, and no invalid shell is accepted by bypassing its validator.

## Shared timing and Z assignment

The skeleton backend provides event-node times. `SkeletonArc.times` already stores their linear interpolation at clipped arc endpoints; arc component/node IDs and clipping fractions now preserve that provenance explicitly.

`_growth_times` resolves each shared growth vertex once. Source-boundary time is exactly zero. Skeleton endpoints use their recorded event/arc time; a point inserted along an arc uses its interpolated time. Only an interior point without arc provenance may use a mutually consistent source-edge estimate. Conflicting arc times are rejected, not averaged.

The common height field is

`z(v) = Zs + (Zm - Zs) * t(v) / tmax`,

where `tmax` is the maximum canonical time over the prepared nodes. Zero time maps exactly to Zs and maximum time exactly to Zm. The existing upper hull band continues from Zm to Zt. In the workspace, Zm remains `Zs + 0.8 * ΔZ`. These are transition coordinates, not terrain elevations.

The backend rounds event times and treats near-collinear edges numerically. Incident source-edge distances are checked against its documented `DISTANCE_EPSILON` and `TIME_EPSILON` (both 1e-5 in the installed version). These backend precision bounds are distinct from shared-coordinate identity and shell-validation bounds. Larger disagreement, missing provenance or contradictory arc times raises a structured `skeleton_time_conflict` diagnostic.

Successful operation metadata contains the maximum edge-time discrepancy, backend precision constants and timing-source counts under `operation.diagnostics.skeleton_timing`. Numerically collapsed column rings are recorded separately under `collapsed_arrangement_rings`. Remaining excess column incidence reports the edge, all incident columns and their owners as `transition_column_incidence`.

## Independent numerical checks

`AggregationLayer.horizontal_section` retains the ideal source-edge-distance wavefront reference, including its independently calculated maximum time. It is not silently redefined to match the new surface.

`AggregationLayer.event_time_section` independently clips immutable pre-allocation growth triangles against a requested Z. It uses the planned event-time field and does not read live DHE vertices. Actual DHE sections must match this planned section at the existing strict numerical thresholds. Differences from the ideal edge-plane reference are measured separately against the backend's precision; shell, volume, cap and interface checks are unchanged.

Consequently, previously valid irregular examples can have small intermediate-coordinate and volume changes. The paper aggregation fixture retains its 68 connecting facets and 121 total facets; its volume changes from approximately 8520.712540787 to 8520.712553838. This is a timing-field correction, not a change in source/target geometry or cell topology. The exact volume regression is updated for that field, while the old edge-plane reference remains available for comparison.

## Regression coverage

```powershell
.\.venv\Scripts\python.exe -B -m unittest test_aggregation test_aggregation_layer test_aggregation_conformity -q
$env:DHE_GUI_TESTS='1'
.\.venv\Scripts\python.exe -B -m unittest test_generalization_visualization -q
```

The reported selections are tested at source levels 5 and 25 with increments 1, 10 and 20. The level-25 fixture uses the same original XY footprints extruded to that source level; it does not claim to reconstruct an unknown earlier sequence of footprint edits. Checks cover source/hull preservation, monotonic intermediate sections, hull completion at Zm, positive volumes, historical sections and unchanged live state during preview.

Additional tests reject contradictory timing provenance, distinguish a collapsed ring from a real narrow region, retain preflight/allocation rollback checks, and exercise filled/wireframe figures, hidden/restored slice coordinates, zero/custom spacing and publication exports. Generated diagnostics and figures remain under ignored `artifacts/`.
