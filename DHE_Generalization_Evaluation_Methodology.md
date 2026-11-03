# Quantitative Evaluation of Applied DHE-Based Generalization

## 1. Purpose, evaluation unit, and provenance

The evaluation characterizes each successfully applied generalization operation through a small set of geometric descriptors and measurements of its realized Dual Half Edge (DHE) representation. It is observational: it neither generates target geometries nor modifies transition facets, topology, or history. Numerical values are obtained from the preceding and applied states, rather than from illustrative geometries, random samples, or predicted outcomes. All computations use floating-point arithmetic and therefore retain its numerical limitations.

The evaluation unit is one successful GUI **Apply** event. Previewing an operation, loading data, restoring a history state, and Undo do not generate evaluations. For simplification, smoothing, and dimensional collapse, geometric descriptors are calculated separately for every selected feature. For merging and aggregation, the selected features form one evaluation group. Measurements describe the current source representation immediately before the operation, not necessarily its original Shapefile geometry.

Every event receives a unique evaluation identifier and records its parent evaluation, session identifier, history index, UTC timestamp, operation, selection, affected features, winner and retired identities, parameters, feature lineage, and transition levels. A dataset fingerprint hashes the accepted projected geometries and indices together with their CRS. The source, CRS, local-coordinate origin, and geometry-library versions are retained. Reports do not embed the original dataset; independent reconstruction consequently requires that dataset and the corresponding operation sequence. A history index alone is insufficient to identify an event because a new Apply after Undo may reuse the same index on another branch.

## 2. Notation and measurement conventions

Let \(S\) denote a selected feature's source polygon and \(T\) its applied top geometry. For merging and aggregation, let \(S_1,\ldots,S_k\) denote the selected sources and \(U=\bigcup_{i=1}^{k}S_i\) their union. The operators \(A\), \(P\), and \(L\) denote polygon area, polygon boundary length, and polyline length, respectively. The symbol \(\triangle\) denotes symmetric difference, \(d\) minimum Euclidean distance, and \(\|\cdot\|\) Euclidean norm.

The centroid \(c(G)\) is the area-weighted centroid for a polygon, the length-weighted centroid for a polyline, and the point itself for a point geometry. The equivalent-area radius of the source is

\[
r_S=\sqrt{A(S)/\pi}.
\]

XY coordinates use the workspace's projected metre CRS; translation to a local origin does not affect the selected descriptors. The Z coordinate represents progression between spatial representations. It is not terrain elevation, physical building height, elapsed time, or map scale unless separately calibrated. Thus the reported transition volume has units of \(\mathrm{m}^2\times\text{transition-Z}\), not physical cubic metres.

The planar vertex count \(n(G)\) counts the stored top polygon's exterior coordinates, excluding its repeated closing coordinate. It is distinct from the number of DHE vertices, which also includes necessary junctions and historical topology. No additional simplification, resampling, smoothing, or target construction is performed during evaluation.

## 3. Operation-specific geometric descriptors

### 3.1. Simplification

**Vertex reduction** quantifies reduction of the planar representation:

\[
R_n=100\left(1-\frac{n(T)}{n(S)}\right)\%.
\]

A positive value indicates fewer vertices; zero indicates no count reduction. It expresses representational economy, but does not establish geometric fidelity. A negative value is retained rather than forced to zero.

**Relative symmetric-difference area** measures the extent of footprint alteration:

\[
E_{\triangle}=100\frac{A(S\triangle T)}{A(S)}\%.
\]

This descriptor accounts for both removed and added areas and avoids cancellation between them. Zero indicates area-equivalent footprints within numerical precision. It complements vertex reduction by exposing the geometric cost of a simpler representation. It is an area discrepancy, not a maximum boundary displacement or an error relative to independent ground truth.

### 3.2. Smoothing

**Compactness change** is defined using the isoperimetric quotient:

\[
C(G)=\frac{4\pi A(G)}{P(G)^2},\qquad \Delta C=C(T)-C(S).
\]

The dimensionless quotient relates enclosed area to boundary length and is invariant to uniform scaling. Positive \(\Delta C\) indicates an increase in compactness; negative values indicate a decrease. The descriptor is appropriate for observing the area–boundary relationship affected by the implemented corner-cutting operation. It is not a curvature measure: elongated and semantically appropriate shapes may have low compactness, and greater compactness does not necessarily imply superior cartographic quality.

Smoothing also reports \(E_{\triangle}\), defined above. Together, the two descriptors distinguish a change in boundary economy from the associated footprint alteration. Neither is assigned a universal acceptance threshold.

### 3.3. Collapse to point

**Normalized centroid displacement** measures positional departure from the source's area centroid:

\[
D_c=\frac{\|c(T)-c(S)\|}{r_S}.
\]

Normalization permits comparison across feature sizes. A value of zero places the target at the area centroid, but that is not always the appropriate feasible target: the centroid of a concave polygon may lie outside its visibility kernel or outside the polygon itself.

**Normalized boundary clearance** describes how far the applied target lies from the source boundary:

\[
Q_p=\frac{d(T,\partial S)}{r_S}.
\]

For the application's validated interior targets, larger clearance indicates greater separation from the boundary. This complements centroid displacement when the visibility constraints require an off-centre point. It does not establish visibility from every source vertex or identify a maximum-clearance point; those are different questions. Evaluation retains the target actually produced by the existing collapse algorithm.

### 3.4. Collapse to polyline

Polyline collapse reports \(D_c\) using the target's length-weighted centroid. This records positional change without depending on arbitrary subdivision of a straight segment.

**Relative line length** relates the realized target length to source extent:

\[
R_L=100\frac{L(T)}{D(S)}\%,
\]

where \(D(S)\) is the longer side of the source's minimum-area enclosing rectangle. The rectangle is allowed to rotate and is not restricted to the coordinate axes. The ratio describes the extent of the line representation relative to a characteristic source dimension. A bent line may produce a value greater than 100%; this is retained. The descriptor is not a spatial coverage percentage, a skeleton-completeness measure, or a criterion that rewards longer lines. It applies to the actual selected ridge, including the existing geometric fallback when used, without treating that fallback as a straight skeleton.

### 3.5. Merging

**Union-preservation error** measures whether the final winner represents the selected source union:

\[
E_U=100\frac{A(T\triangle U)}{A(U)}\%.
\]

The expected result is zero within floating-point precision because the implemented merging target is the union. This is an operation-fidelity descriptor rather than an independent assessment of real-world correctness. Losing identities may remain as historical DHE cells even though they disappear from the top map.

**Total boundary-length reduction** measures the change from individually represented sources to the combined target:

\[
R_P=100\left(1-\frac{P(T)}{\sum_{i=1}^{k}P(S_i)}\right)\%.
\]

The denominator deliberately sums feature perimeters. A shared source boundary contributes to both adjacent features before merging. Accordingly, the descriptor captures elimination of internal feature boundaries as well as changes in the outer boundary. It must not be interpreted as a comparison against the perimeter of the dissolved source union.

### 3.6. Aggregation

**Relative area expansion** measures the additional area introduced between the selected sources:

\[
R_A=100\frac{A(T)-A(U)}{A(U)}\%.
\]

For the implemented convex-hull aggregation, this describes gap filling and the resulting loss of separation between sources. Zero indicates no area expansion; positive values quantify its magnitude. It is not an error against ground truth, nor does a smaller value necessarily indicate a preferable aggregation.

Aggregation also reports \(R_P\). Boundary reduction may be negative when connecting distant sources creates a sufficiently long target boundary. Values are not clipped to a presumed desirable interval.

## 4. DHE construction measurements

### 4.1. Affected-cell complexity

The evaluation scope is the union of the recorded affected identities, selected identities, and resulting winner or aggregate identity. Before and after measurements include whichever of those identities have realized bounded cells in the corresponding model. The unbounded exterior and dual cell-centre vertices are excluded.

For every included cell \(c\), primal vertices are identified by their actual DHE vertex objects, undirected edges by unordered endpoint pairs, and faces by distinct traversed face cycles. Totals are

\[
N_V=\sum_c|V_c|,\qquad N_E=\sum_c|E_c|,\qquad N_F=\sum_c|F_c|.
\]

The GUI reports each total before and after Apply, together with its difference. Shared vertices, edges, and interfaces contribute once for each incident included cell. These totals measure **per-cell representational complexity**, not globally unique mesh entities or process memory usage. They include preserved history, rather than an independently clipped mesh for the latest band. Reports also retain the individual cell counts.

This convention exposes the additional topology required for transition construction and the effects of historical ownership. An operation that simplifies the top map can still increase DHE complexity. These counts do not independently certify manifoldness, pointer consistency, or absence of self-intersection; the application's existing validators remain responsible for those checks.

### 4.2. Added transition volume

Volume is calculated from the actual oriented planar boundary faces. For a face cycle \((v_0,\ldots,v_{m-1})\), its signed triangle-fan contributions are accumulated over the closed shell:

\[
V(c)=\left|\frac{1}{6}\sum_{f\in F_c}\sum_{j=1}^{m_f-2}
v_{f,0}\cdot\left(v_{f,j}\times v_{f,j+1}\right)\right|.
\]

The fan is an integration device; it creates no DHE triangles and does not alter polygonal faces. Signed contributions remain necessary for concave planar faces.

The added transition volume is

\[
\Delta V=\sum_{c\in\mathcal C_{\mathrm{after}}}V(c)
-\sum_{c\in\mathcal C_{\mathrm{before}}}V(c),
\]

using the affected-cell scope defined above. Because the application preserves the occupied geometry below the preceding source level, subtracting the historical volume isolates the bounded occupied volume added by the new interval. Selected cells and changed neighbours are included together. Aggregation's remapping of historical source identities to the aggregate is accounted for by summing the relevant cells on each side, rather than requiring identical identifiers.

This quantity describes constructed transition-space occupancy. It depends on the chosen Z increment and boundary construction, so comparisons should retain those parameters. Larger or smaller values do not have an intrinsic quality ranking. The measure excludes unbounded exterior space and is subject to floating-point cancellation when two large historical volumes are subtracted.

## 5. Reporting, undefined values, and reproducibility

Every Apply produces a separate JSON/CSV pair under a unique session/event directory. Files are published only when the pair has been written; an existing event is never overwritten. JSON contains full metadata, raw geometric properties, per-cell counts and volumes, and metric rows. CSV contains one row per metric with event identifiers, operation, selection, affected identities, winner, parameters, levels, dataset context, value, unit, and applicable before/after counts. Numerical values are saved without the GUI's display rounding.

Reports remain available after Undo and after a new operation replaces an active history branch. Parent event identifiers preserve the original sequence. The viewer can load saved reports from earlier application runs. Merely reviewing a report does not reconstruct or change its DHE model.

Undefined or non-finite measurements are recorded as unavailable, with a null numerical value and an explanatory reason. They are not replaced by zero. Unexpected evaluation failures produce a failed evaluation record without fabricated metric values. A file-system failure leaves the numerical result available in memory for retry and does not undo the completed operation. Pending evaluation is allowed to finish saving before the GUI closes normally.

The system does not compute a composite quality score, invent acceptance thresholds, compare against external reference data, or estimate construction performance from other operations. Meaningful comparisons must account for operation, dataset, feature size and shape, selection, neighbours, Z increment, and prior history.

## References to measurement semantics

- Shapely contributors. [Centroid: area- and length-weighted geometric centres](https://shapely.readthedocs.io/en/stable/reference/shapely.centroid.html).
- Shapely contributors. [Minimum rotated rectangle: minimum-area oriented enclosure](https://shapely.readthedocs.io/en/stable/reference/shapely.minimum_rotated_rectangle.html).
- Shapely contributors. [Measurement operations](https://shapely.readthedocs.io/en/stable/measurement.html).

These references specify library measurement semantics. The operation-specific selection and interpretation of descriptors above constitute the evaluation protocol implemented in this application.
