# Manual Quantitative Evaluation of 2D+1D LoD Transition Space

## 1. Evaluation protocol and comparison units

Quantitative analysis is initiated explicitly through **Quantitative Evaluation → Evaluate**. Applying an operation performs the existing construction validation but does not calculate quantitative evaluation results or save evaluation files. **Save results** is a separate explicit action. Opening the evaluation window, Preview, loading, Undo, and history navigation do not initiate analysis.

**Last operation** evaluates the applied transition ending at the currently displayed history state. It reports selected features individually for polygon transformation and collapse, a selected group for merging and aggregation, an additional winner profile for merging, and an affected-context summary including changed neighbours. **Whole history** evaluates the active branch from its initial top representation to the currently displayed state. Later stored history states are excluded after Undo. It reports both the entire map and the changed-lineage subset, including selected ancestors and affected neighbours. It uses only common metrics; heterogeneous operation-specific descriptors are not averaged.

Each history state has a stable identifier and parent identifier. Each requested evaluation has a new identifier. Reports retain dataset source, projected CRS, origin and fingerprint, selected/affected indices, lineage-derived scope, operations, parameters, comparison steps and Z levels, numerical settings, and library versions. Reusing a history index on another branch does not reuse its state or evaluation identity. Existing version-1 automatic-evaluation reports can be reviewed as legacy records without reinterpretation or rewriting.

### Optional feature-family selection

The default **All features** request preserves the standard scopes above. **Choose features** selects one or more current or retired feature IDs from the active history prefix before measurement. **Consider feature family** is checked by default. Families are connected through recorded lineage: ancestors, merge contributors, intermediate aggregates and merged results belong to the same family. No later history state is consulted after Undo. This is a complete-family analysis, not an attempt to attribute part of an indivisible merged polygon to one contributor.

When **Consider feature family** is unchecked, scopes contain only the exact requested IDs, individually and together. Duplicate requested IDs are counted once. A retired ID can disappear from physical sections, a new aggregate ID can lack an earlier reference, and a reused merge-winner ID describes its actual enlarged geometry. Missing geometry or references yield the existing explanatory N/A measurements; ancestors or descendants are never substituted. Provenance lineage may still be recorded for an exact ID without being included in measured cell membership. Reports and profile metadata distinguish `exact_ids` from `full_family`. Changing the checkbox affects only a new explicit Evaluate request; it is disabled for the standard All features request.

Each distinct requested family (or exact ID when unchecked) receives an individual scope. Multiple distinct scopes also receive a combined scope; common cell IDs and identical families are deduplicated. Requested IDs, additional family cells and resolved initial lineages are disclosed in report metadata and numerical exports. Last-operation references remain the preceding source representation; whole-history references remain the initial top. A case descriptor is calculated only when the selected scope matches a relevant action; unrelated or multi-action scopes receive an explanatory N/A.

Individual family polygon metrics are N/A when the actual representation is collapsed or missing. Combined scopes report polygonal coverage totals, with point counts and line lengths separate. Complexity counts and shell checks use selected family cells; shared-interface checks include the real counterpart without adding that counterpart to selected counts. The live primal/dual audit always validates the complete stored complex and is explicitly labelled as global context. Existing report schemas and metric formulas remain unchanged; feature-selection metadata and scoped complexity rows are additive.

### Comparing two measured profiles

**Overlay two profiles** selects two distinct evaluation/scope pairs from the current result or saved version-2 evaluations. One figure overlays them in blue and orange across the area-change, perimeter-change and sampled-NHD panels. The measured values and actual Z coordinates are retained, including different Z ranges, separate History-step segments and N/A gaps. No section geometry or metric is recalculated and no temporal alignment or interpolation is introduced. Each profile retains its own reference geometry and normalization, so differing reference Z levels and bounding-box diagonals are disclosed in the figure and export metadata. The comparison JSON preserves source evaluation IDs, datasets, scope definitions, feature-selection policies, reference details and plotted samples, including for an unsaved current evaluation. This is a graphical comparison of existing measurements, not a new combined-feature evaluation or a common-baseline error estimate.

## 2. Actual representations and dimensional conventions

All evaluated representations are horizontal sections reconstructed from immutable, realized DHE boundary records. Historical models are not reallocated, and global DHE topology is not restored or mutated for evaluation. The current model is also audited through its live primal and dual relationships. No target or section geometry is numerically interpolated from endpoint measurements.

Every transition interval is evaluated at

\[
z_j=z_s+t_j(z_t-z_s),\qquad t_j=j/10,\quad j=0,\ldots,10.
\]

Thus nine interior sections and both endpoints are evaluated. Whole history retains eleven sections per operation, including unequal Z increments. Repeated boundary levels belong to their respective interval endpoints. At a shared horizontal interface, **below** selects the source-side representation and **above** selects the upper representation for inspection. This convention is based on oriented face incidence, not lexical feature-ID order. Evaluation uses below-side ownership for historical and endpoint comparisons.

Let \(G_j\) be a scoped collection of actual section geometries, and let \(G_0\) be its reference representation. Last-operation comparisons use the preceding source state. Whole-history comparisons, including profiles, use the initial top state and a fixed reference normalization for each scope. The initial level is the initial extrusion's roof, not the extrusion bottom.

For polygon-to-polygon comparisons, area and perimeter describe polygon representations. A collapsed point or polyline has no polygon perimeter or polygon-area quality metric: those individual-target entries and their polygon changes are **N/A**. Whole-map and affected-context totals instead describe their polygonal components; zero polygonal coverage in an all-symbol map is meaningful, but does not imply that point or line symbols are absent. Point counts and polyline lengths are reported separately. Missing physical representations are not synthesized from logical map entries.

## 3. Common geometric measurements

### 3.1. Area and perimeter differences

For the polygonal components \(\mathcal P(G)\), define

\[
A(G)=\sum_{p\in\mathcal P(G)}A(p),\qquad
P(G)=\sum_{p\in\mathcal P(G)}P(p).
\]

Area uses square metres and perimeter uses metres in the workspace's projected CRS. A shared polygon boundary contributes to each incident feature's perimeter. The measure therefore captures removal of internal feature boundaries during merging; it is not the perimeter of a dissolved union. Holes, where present in an actual section, contribute their boundary lengths.

The signed differences and relative differences are

\[
\Delta A_j=A(G_j)-A(G_0),\quad R_{A,j}=100\Delta A_j/A(G_0),
\]
\[
\Delta P_j=P(G_j)-P(G_0),\quad R_{P,j}=100\Delta P_j/P(G_0).
\]

Relative changes are percentages; division by a zero reference is undefined and reported as N/A. Positive and negative values indicate expansion and reduction, respectively. These are representational changes rather than errors against independent ground truth. Equal areas or perimeters do not imply equal shapes.

### 3.2. Hausdorff and normalized Hausdorff displacement

Define the displayed geometric support \(B(G)\) as the union of polygon boundaries, actual polyline geometries, and actual point geometries. Polygon boundaries are used rather than filled polygons so that boundary removal and movement remain observable. Coincident linework is geometrically deduplicated for displacement, independently of the per-feature perimeter convention.

For nonempty supports \(X=B(G_0)\) and \(Y=B(G_j)\), continuous symmetric Hausdorff distance is

\[
H(X,Y)=\max\{\sup_{x\in X}d(x,Y),\sup_{y\in Y}d(y,X)\}.
\]

The implementation calculates a controlled sampled form. Every original segment is subdivided to a maximum spacing \(h=\alpha D\), with default \(\alpha=0.001\). Here \(D\) is the diagonal of the reference support's axis-aligned bounding box. Original vertices and isolated points are retained. Samples are queried against spatially indexed **original target segments or points**, not merely the target sample vertices:

\[
H_h(X,Y)=\max\{\max_{x\in Q_h(X)}d(x,Y),\max_{y\in Q_h(Y)}d(y,X)\},\qquad
NHD_h=H_h/D.
\]

\(H_h\) has metre units and \(NHD_h\) is dimensionless. Both sampling directions are used. For piecewise-linear supports with maximum sample gap \(h\), the distance-to-set function is 1-Lipschitz, giving the sampling-only bound

\[
H_h\le H\le H_h+h/2.
\]

This bound does not include floating-point or section-reconstruction errors. Results are explicitly labelled sampled Hausdorff, rather than exact continuous Hausdorff. Reports retain \(D\), \(h\), \(h/2\), and sample counts. A zero reference diagonal makes normalization undefined; an empty actual support makes displacement undefined. The default sample budget is 200,000 samples per representation in a comparison. Exceeding it produces N/A and a reason; the implementation never silently coarsens the requested sampling.

Displacement after dimensional collapse describes an intentional change of representation, not exclusively positional error. Similarly, removing an internal boundary during merging can increase Hausdorff displacement despite unchanged union coverage. It is therefore not assigned a universal quality threshold.

## 4. Concise operation-specific descriptors

Only Last operation includes these descriptors. Let \(S\) and \(T\) be actual source and target representations, \(U\) the selected source union, \(n\) exterior vertex count excluding ring closure, and \(c\) the geometry centroid.

| Operation | Descriptor and formula | Interpretation and limitation |
|---|---|---|
| Simplification | Vertex reduction \(100(1-n(T)/n(S))\%\) | Planar representation economy; DHE junction counts are measured separately. |
| Smoothing | Compactness change \(4\pi A(T)/P(T)^2-4\pi A(S)/P(S)^2\) | Change in area–boundary economy; not a curvature measure or universal smoothness score. |
| Collapse to point | \(\|c(S)-T\|/\sqrt{A(S)/\pi}\) | Positional departure relative to equivalent-area radius; the area centroid need not be a feasible collapse target. |
| Collapse to polyline | \(100L(T)/D_{rect}(S)\%\) | Line length relative to the long side of the minimum-area enclosing rectangle; not spatial coverage, skeleton completeness, or a reward for length. Values above 100% are retained. |
| Merging | \(100A(T\triangle U)/A(U)\%\) | Agreement with the implemented union target; expected near zero within numerical precision. |
| Aggregation | \(100A(T\triangle CH(U))/A(CH(U))\%\) | Agreement with the implemented hull target. Area expansion itself is already measured by the common area difference. |

Vertex reduction uses the actual section rings. Conforming junctions or collinear section vertices can therefore contribute to these planar counts; this is not a count of retained raw Shapefile vertices. The separate 3D complexity table measures the complete realized cell complex.

Descriptors use applied output, including any accepted existing target-search fallback. They do not re-run generalization. Undefined descriptors include an explanatory N/A instead of an invented numerical value.

## 5. Structural complexity and transition-space validity

### 5.1. Retained LTS versus top-supporting cells

**Retained LTS complexity** counts globally unique primal vertices \(N_V\), undirected primal edges \(N_E\), shared geometric faces \(N_F\), and bounded cells \(N_C\) across the complete stored realized complex. Coincident opposite uses of one inter-cell face count once. The unbounded exterior is excluded. Coordinates are the already conformed, realized coordinates; evaluation introduces no new snapping or clipping.

**Top-supporting complexity** applies the same global-entity counting convention to the complete closed cells supporting the actual active logical representation at that history level. These are full cell shells, not the V/E/F counts of a manufactured planar slice mesh. Logical feature counts and actual top-feature counts are reported independently. Historical checkpoints use the model as stored at that step, rather than assigning later subdivisions retrospectively to earlier checkpoints.

Consequently, three source cells aggregated into one target can produce one top-supporting aggregate cell while retaining four bounded cells in the LTS. Planar generalization need not reduce retained 3D topology. These quantities are not process memory measurements.

### 5.2. Many-to-one aggregation topology

The lower source cells retain their identities, dual vertices, boundaries, and roofs at \(z_s\). A distinct upper aggregate cell occupies the new transition interval. Every preserved source roof is paired with an oppositely oriented bottom face of the upper cell. Existing lower neighbour interfaces remain present. Repeated aggregation preserves earlier aggregate cells and their predecessors.

Coordinate plans are realized using the existing `MEVVFS → MEV → MEF` face sequence, `Sew` within cells, and `JoinByFace` across matching interfaces. `JoinByFace` establishes shared primal/dual relationships while retaining distinct cell duals; it is not used as dual contraction. Skeleton times, growth facets, hull targets, and total occupied geometry are retained. Total volume sums all bounded cells, whereas the upper aggregate's volume excludes the lower source interval.

### 5.3. Validation measures

Missing edges have one incident face in a cell; non-manifold edges have more than two. An edge with two uses is misoriented unless its directions are opposite. These counts are **per cell**: an edge shared by several valid cells is not automatically non-manifold. Closed-cell checks additionally inspect connectedness, cyclic vertex links, and the supported genus-zero Euler relation \(V-E+F=2\). Interface checks require distinct owners and opposite face cycles.

Historical validation audits immutable realized boundaries. Only the live model is checked for reciprocal symmetric/dual pointers, face continuity, cell-dual ownership, and reciprocal shared-interface adjacency. Historical rows explicitly state that live pointers were not rechecked. These are numerical/combinatorial checks, not exact-arithmetic certificates of all possible geometric intersections.

Logical map entries are compared with the actual DHE section. For example, a terminal point may remain in the logical map after a later unrelated operation even though its bounded DHE cell terminates at an earlier level. Such missing terminal representations are reported explicitly and not inserted into the evaluated geometry.

Construction time and memory are not reconstructed retrospectively. `evaluation_elapsed_seconds` measures the requested analysis, not generalization construction performance.

## 6. Profile summaries, exports, and paper presentation

For each scope, report the maximum of the sampled NHD values and a sampled Z-weighted mean:

\[
\overline{NHD}_{Z}=\frac{\sum_i\Delta z_i[\tfrac12h_{i,0}+\sum_{j=1}^{9}h_{i,j}+\tfrac12h_{i,10}]/10}{\sum_i\Delta z_i},
\]

where \(h_{i,j}\) denotes measured normalized displacement, not sample spacing. This is a trapezoidal summary of sampled metric values, not an exact continuous integral. When samples are unavailable, their weights are omitted, the remaining weights are renormalized, and valid/requested coverage is reported. Counts and heterogeneous case descriptors are not averaged.

Save produces a unique directory containing JSON metadata, `summary.csv`, `sections.csv`, `structure.csv`, and `analysis_report.md`. CSV values retain numerical precision rather than GUI display rounding. The separate analysis report describes the analyses actually performed, their actual input levels and parameters, formulas, values, interpretation, and limitations. Saving again to an occupied event directory cannot overwrite the previous report; a new Evaluate request creates a new evaluation identity.

The most useful main-paper table is endpoint/history area and perimeter change with sampled HD/NHD. A second table should contrast retained and top-supporting V/E/F/C counts and validation outcomes. Recommended figures are the measured area/perimeter/NHD profiles against actual Z and a verified primal/dual aggregation diagram showing lower sources, source-level interfaces and the upper aggregate. Full eleven-level tables and parameter metadata are suitable for supplementary material. Profile figures reuse the publication export settings, including Times New Roman and vector SVG/PDF support.

## References to computational semantics

- [Shapely: sampled/discrete versus continuous Hausdorff distance](https://shapely.readthedocs.io/en/stable/reference/shapely.hausdorff_distance.html).
- [Shapely: controlled maximum-length segment subdivision](https://shapely.readthedocs.io/en/stable/reference/shapely.segmentize.html).
- [Shapely: nearest-geometry spatial indexing](https://shapely.readthedocs.io/en/stable/strtree.html).

These references specify library semantics. The metric selection, comparison scopes, reporting conventions, and interpretation above define the application's evaluation protocol.
