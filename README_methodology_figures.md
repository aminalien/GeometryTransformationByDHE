# DHE Methodology Figure Generator

This independent paper visualization tool computes four documented synthetic
examples through the current production geometry and DHE builders. It does not
read private Shapefiles or change existing transformation routines. Run it from
the project directory with the Python interpreter configured for this project.

```powershell
.\.venv\Scripts\python.exe methodology_figure_generator.py
.\.venv\Scripts\python.exe methodology_figure_generator.py --no-gui --export-all
.\.venv\Scripts\python.exe methodology_figure_generator.py --no-gui --case p2p --stage 2 --panel region_classification --export
.\.venv\Scripts\python.exe methodology_figure_generator.py --no-gui --case p2pt --stage 5 --export --dpi 600
.\.venv\Scripts\python.exe -m unittest test_methodology_figures -v
```

Dependencies are those already used by the project: NumPy, Shapely, GeoPandas,
Matplotlib and py_straight_skeleton, plus Python's Tkinter for the desktop GUI.
Headless exports use Matplotlib's Agg canvas and do not require a display.
No new dependencies are required. The inspected environment used Python 3.12,
Matplotlib 3.11.2 and py_straight_skeleton 0.1.0.

## Controls and exports

Choose one of the five exact manuscript stages, a case, and either Combined
panels or an individual panel. Auto view uses each panel's appropriate 2D/3D
projection; 2D/3D and camera controls apply only where meaningful. Pure geometry
plans do not expose primal DHE edges or dual incidence as if already realized.
Disabled controls have no applicable data; the description panel explains this.
Publication labels are on initially; switching them off hides annotations and
never reveals original identifiers. Vertex/edge labels are a legible subset of
stable local names, rather than internal DHE identifiers. The Matplotlib toolbar
supports navigation; the plot area can be scrolled for taller combined figures.

Export current view preserves its toggles, selected panel and camera preset.
Export All uses the documented default style for every combined and individual
panel. Files have deterministic names such as
`stage2_p2p_region_classification.svg`. SVG and PDF are vector outputs; PNG is
300 dpi by default, with 600 dpi available. Exported figures contain no GUI
widgets. Default combined figures are 7.2 inches wide, with row-dependent height.

The output directory is self-contained: figures, `figure_captions.md`,
`figure_manifest.json`, `example_metadata.json`, `visualization_status.md`,
`export_report.json` and this README. The manifest stores public labels, routine
names, settings and checks only. It never stores a raw identifier mapping or
private input paths. Generated assets stay local under the repository workflow;
rerun Export All to reproduce them. Individual exports update rather than discard
other manifest/caption entries. Re-exporting a view replaces that view's files.

## Consistent examples and levels

- **Single-source cases:** Source S is the square from (1,1) to (3,3). Four
  neighboring polygons N1–N4 partition the surrounding (0,0)–(4,4) domain.
  P2P Target T is the rectangle (2,1.5)–(3.5,2.5), giving nonempty source-only,
  target-only, common and background regions. P2Pt uses interior point (2,2).
  P2L uses [(1.6,2.4), (1.5,1.8), (2.4,1.7)].
- **Aggregation:** Source A is (0,0)–(2,3), B is (6,1)–(8,4), and C is
  (3,6)–(5,8). Target H is their computed union's convex hull. The final output
  is one aggregate DHE cell. Source colors on its faces represent provenance.
- Test levels are z_b=0, z_s=1, z_t=2, with aggregation z_m=1.8. Figures use
  symbolic ticks. Z is an ordered transition/LoD coordinate, not terrain height
  or a calibrated numeric map scale. Intermediate sections use z=1.5 for the
  single-source cases and z=1.4 during aggregation growth.

## Data and operator evidence

Stage 1 freezes actual base cells. Stage 2 freezes the actual polygon overlay
before retirement, or reads point/polyline preparation and skeleton ownership.
Stage 3 reads production facet plans. Stage 4 captures actual operator entry and
return effects through a scoped execution profiler; it does not replace operator
definitions. Stage 5 freezes actual realized faces and extracts sections with
`PlaneSlicer.sections(..., include_degenerate=True)`.

`MEVVFS` initializes a complex edge using supplied primal/cell/exterior vertices;
`MEV` attaches a supplied vertex, and `MEF` closes the selected boundary chain.
The three construction panels concern one representative transition face and
are not yet a closed cell. `Sew` connects compatible within-cell uses.
`JoinByFace` subsequently connects matching interfaces in single-source cases
while retaining distinct cell dual identities. Aggregation constructs one new
cell with one aggregate dual vertex: no JoinByFace merger, dual contraction or
KEF call is claimed. The operation counts exclude calls nested inside another
core operation, so a Sew nested in JoinByFace is not counted twice.

Dual associations are checked from `e.D.V` and `e.Adjacent().D.V`; coordinates
are the actual dual-vertex coordinates. They are not a computed Voronoi dual.
Primal and dual pointers are checked through the production validators.
The adapter restores the caller's core globals, counters and profiling hook in
finally blocks. It is intended for sequential desktop/headless use, not concurrent
construction in multiple threads. Snapshots contain no live DHE records.

## Limits and interpretation

All 20 default stage/case selections have actual computed data. There are no
schematic fallback surfaces. A capture failure is reported as unavailable and
never replaced by an invented result. Export failures are recorded separately in
`export_report.json`. The supported examples do not establish validity for every
possible input geometry or an arbitrary external SSC/LTS.

Temporary P2P roof triangulation differs from final inclined facet planning.
P2Pt and P2L preparation do not execute MVE; polyline noding changes coordinate
plans. Aggregation preparation executes no DHE operators. Facets are continuous
piecewise-planar surfaces; tangent continuity is not claimed. Terminal point and
ridge contacts are actual slicer outputs, not zero-area polygon caps. The ridge
may be a MultiLineString geometrically equal to the input polyline.

Validation annotations name checks that ran for that example. Single-source
coverage is tested at the seven production transition levels, not every possible
plane. Additional planarity and planned/realized transition-facet comparisons run
in the adapter. Aggregation checks one cell, vertex links, hull cap and planned
volume; no universal all-pairs 3D intersection certificate is asserted.

## Visual review checklist

1. Select all 20 combinations; inspect combined and individual panels.
2. Confirm distinct preparation, plan, realization and final views.
3. Check P2P Stage 2's four-class legend and exact explanatory annotation.
4. Check source/target and local labels remain consistent across stages.
5. Inspect Stage 4 partial chains, sewn interfaces and actual dual associations.
6. Inspect Stage 5's 3D result, intermediate section and terminal geometry.
7. Check legends, panel letters, holes, line/point contacts and text at journal size.
8. Review the public manifest, status table and export report for completeness.
