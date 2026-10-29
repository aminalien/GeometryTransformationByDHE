# DHE Methodology Figure Generator

This independent paper visualization tool computes four documented synthetic
examples through the current production geometry and DHE builders. It does not
read private Shapefiles or change existing transformation routines. Run it from
the project directory with the Python interpreter configured for this project.

```powershell
.\.venv\Scripts\python.exe methodology_figure_generator.py
.\.venv\Scripts\python.exe methodology_figure_generator.py --no-gui --export-all
.\.venv\Scripts\python.exe methodology_figure_generator.py --no-gui --main-figures --dpi 600
.\.venv\Scripts\python.exe methodology_figure_generator.py --no-gui --case p2p --stage 2 --panel region_classification --export
.\.venv\Scripts\python.exe methodology_figure_generator.py --no-gui --case p2pt --stage 5 --export --dpi 600
.\.venv\Scripts\python.exe -m unittest test_methodology_figures test_methodology_publication -v
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
600 dpi by default. The editable DPI field offers 300, 600, 1200 and 2400 and
accepts higher positive values. Exported figures contain no GUI widgets.
Default working figures are 10 inches wide, with automatic height and larger
3D panels. The GUI preview uses at most 100 dpi, reduced for very large canvases,
independently of PNG export DPI.

## Five main manuscript figures

Choose **Manuscript Figures 2–6** under Figure set. The Stage dropdown selects
the corresponding manuscript figure; Case and Panel are disabled because the
main layouts have fixed scientific panel recipes. Selection, preparation and
DHE integration use representative process panels. Facet planning compares four
cases. Final extraction has four rows (P2P, P2Pt, P2L, AGG) and three columns
(actual DHE, intermediate section, terminal geometry), with one shared legend.
All views reuse the existing immutable computed snapshots. The captions in
`main_figure_captions.md` match the five captions in the local revised manuscript.

## Publication settings: inches, pixels, DPI and fonts

Open **Publication settings** to edit separate JSON tabs for Fonts, Layout,
Export, View, Panels, Annotations and Views. These are visualization settings;
editing them does not modify computational algorithms. `null` means automatic.
Use **Save settings / positions** and **Load settings** to preserve and restore
the complete settings and current camera/limits. The fully populated template
is `publication_settings.example.json`.

```powershell
# Physical size, arbitrary DPI; dimensions are inches
.\.venv\Scripts\python.exe methodology_figure_generator.py --no-gui --case p2p --stage 5 --panel realized_dhe --export --width 4.5 --height 4 --dpi 1200
# Exact pixel dimensions; DPI sets the physical size and text scale
.\.venv\Scripts\python.exe methodology_figure_generator.py --no-gui --case p2pt --stage 5 --panel terminal_representation --export --width-px 6000 --height-px 6000 --dpi 2400
# Apply a saved style and annotation configuration
.\.venv\Scripts\python.exe methodology_figure_generator.py --config my_settings.json
.\.venv\Scripts\python.exe methodology_figure_generator.py --no-gui --main-figures --config my_settings.json
```

Physical and pixel sizing are explicit alternatives. In the Export tab, set
`size_mode` to `physical` to use Layout `width_in` and `height_in`, or `pixels`
to use `width_px` and `height_px`. If only one pixel dimension is provided,
the original aspect ratio determines the other. CLI inch and pixel overrides
cannot be combined. Pixel dimensions do not change when DPI changes in pixel
mode; physical size and the relative size of text do change.

All figure text uses **Times New Roman**, including mathematical subscripts.
The regular, italic, bold and bold-italic faces must be installed; the renderer
reports missing fonts and does not substitute another family. SVG retains text
elements and references Times New Roman; recipients need that font installed.
PDF embeds TrueType font subsets where supported. Font files are not distributed.

In Fonts, independently change `title`, `panel_title`, `panel_letter`,
`axis_label`, `tick`, `legend`, `name`, `vertex`, `edge`, `z_level`, `z_axis`,
and `footer` (points). The default Z-level size is **16 pt**, versus 10 pt for
ordinary ticks. To enlarge Z labels alone, change `fonts.z_level`.

## Layout, cameras and annotation placement

Layout exposes `width_in`, `height_in`, `plot_height_in`, `legend_space_in`,
`left`/`right` fractional margins, `top_in`/`bottom_in`, `wspace`, `hspace`,
`three_d_width_ratio`, and optional `row_heights`/`width_ratios` arrays. Array
lengths must match the current layout's grid; use per-panel overrides for a
setting intended for one specific view. Legends receive measured space rather
than a fixed large empty band. `show_title` and `show_footer` can hide duplicated
headings or explanatory text when placing the figure in the manuscript.

View settings expose `zoom`, `projection` (`persp` or `ortho`),
`focal_length`, `aspect`, `axis_pad`, `z_offset_points`, `legend_loc` and
`legend_columns`. Zoom changes display scale; it does not alter coordinates.
Z labels are projected callouts attached to their actual transition levels;
their offsets persist when the camera changes.

For individual subplot control, use the Panels tab. Keys are
`view-key/panel-key`; view keys and panel keys are present in the manifest and
saved Views/Annotations tabs. A gallery example is:

```json
{
  "stage5_p2pt_realized_dhe/realized_dhe": {
    "rect": [0.10, 0.17, 0.76, 0.70],
    "elev": 30, "azim": -50, "roll": 0, "zoom": 1.1,
    "xlim": [-0.2, 4.2], "ylim": [-0.2, 4.2], "zlim": [-0.1, 2.2],
    "aspect": [1, 1, 0.8],
    "legend_anchor": [0.5, 0.12], "legend_loc": "upper center", "legend_columns": 3
  }
}
```

Turn on **Edit text**, then drag a feature/vertex/edge label, Z callout, title,
panel letter, axis label or legend. Turn it off to restore 3D rotation. Click
**Reset selected text** to restore that item's automatic placement, or save
settings to keep the adjustment. Manual positions take precedence over automatic
placement and survive view switches and exports. 3D labels retain their true
geometric anchors; dragging adjusts the offset rather than the geometry.
Native automatically positioned X/Y tick labels use font/padding settings rather
than individual drag handles.

The Annotations tab also permits precise adjustments without dragging:

```json
{
  "stage5_p2pt_realized_dhe/realized_dhe/z_level:$z_s$": {
    "offset": [20, 4], "ha": "left", "va": "center", "rotation": 0
  }
}
```

Offsets are points. Titles and panel labels use `position` in their original
figure/axes coordinate system; axis labels use `axes_position`; legends use
`anchor` in figure fractions and `loc`. Main-figure keys start with
`main_figure_N_stageM`; mixed-case panel keys include a case prefix such as
`p2pt:realized_dhe`. Save a dragged example to obtain its exact key.

## Memory and format limits

There is no application DPI ceiling. Before allocating a raster, the exporter
estimates peak memory from its pixel dimensions and compares it with
`export.memory_budget_mib` (initially 1024 MiB) and available memory where
accessible. The preview status shows pixels and estimated MiB. Backend dimension
limits and allocation errors are reported. Adjust the budget in settings or with
`--memory-budget-mib`; increasing DPI alone is unnecessary for vector geometry.

If PNG is unavailable, SVG/PDF remain available and the manifest records the
reason. No lower-resolution PNG is substituted. A pre-existing PNG is not replaced
when a new raster request is rejected; consult the current manifest's per-format
status. Each successfully written format replaces its file atomically. Small
physical canvases with large fonts may need manual font/margin adjustments.

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
