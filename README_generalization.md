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

The loader reports unsupported records explicitly. Initial geometry supports valid, hole-free Polygon features. Multipart features, holes, and invalid geometry remain excluded. Shared boundaries and point contacts are allowed.

If supported input polygons overlap in positive area, Shapefile and OSM loading opens an **overlap review** before constructing any replacement DHE cells. The suggested survivors are chosen by descending projected area, with original row index breaking ties: retain a polygon unless it overlaps an already retained polygon. A chain A–B–C can therefore retain A+C instead of deleting the entire group. Click **Keep?** to change a survivor; keeping one polygon deselects its conflicting neighbours. Highlight compatible rows and use **Keep only highlighted in this group** for choices such as only B or A+C. **Largest first in this group** restores the suggestion. Nonconflicting input is always retained, and at least one polygon must remain in each overlap group. **Load chosen survivors** constructs the new session; **Cancel** preserves the current dataset and history.

Resolution retains whole polygons, their attributes and original indices. It does not trim footprints or edit source files. The Dataset report names removed indices and retained conflicting neighbours where applicable. Headless loading uses the same deterministic largest-first rule and prints its removal notes. Programmatic loaders (`prepare_frame`, `load_shapefile`, `download_osm`) accept `overlap_policy='keep_largest'` (default), `'review'` (return pending conflicts), or `'error'` (strict rejection). Use `resolve_overlaps(data, survivor_indices)` before passing pending review data to `GeneralizationSession`.

Use the mouse wheel over the **current top map** for cursor-centred zoom, or **Zoom in**, **Zoom out**, and **Fit map**. The view survives selection, Preview, Apply, Undo, and history navigation. Loading a new dataset fits its extent automatically. Matplotlib toolbar pan/zoom remains available; map selection is suspended while a toolbar navigation mode is active. Scrolling over the section panel still moves the slicing plane.

## Generalization operations

All operations start from the **actual current DHE top section**. Planning preserves unrounded coordinates and removes only arithmetic-equivalent collinear nodes. Source boundaries are conformed wherever neighbours start or stop along an edge. Shared identities are not created by globally rounding coordinates or increasing a modelling tolerance.

| Operation | Behaviour |
|---|---|
| Simplification | Topology-preserving Douglas–Peucker; tolerance in metres, default 1. |
| Smoothing | Closed-ring Chaikin corner cutting; default one iteration, ratio 0.25. Parameters are validated; vertex growth is bounded. |
| Collapse to point | Uses the centroid of the visibility kernel. The point must be interior and visible from every boundary vertex. Polygons with an empty interior kernel cannot use the single-apex method. |
| Collapse to polyline | Searches straight-skeleton paths for a simple, realizable interior ridge. Uses a principal-axis segment through the visibility kernel if necessary. The chosen method is reported. Branched targets and targets surviving only as zero-volume flaps are rejected. |
| Merging | Select touching polygons and choose a winner. Its current polygon grows smoothly to their exact union using P2P transition facets. Losers shrink in the complementary volume and retire from the top map at the target level. The winner keeps its attributes; lineage records all sources. A disconnected or holed union is rejected. |
| Aggregation | Uses the existing source-driven straight-skeleton growth construction to reach the selected polygons' convex hull. Retains distinct lower source cells and their dual vertices; a new aggregate cell starts at the source level and joins each preserved source roof. Growth completes at 80% of the new interval, followed by the hull cap. |

Simplification, smoothing, and collapse accept several sources in the same interval. Conflicting polygon targets are rejected as a complete batch. **Preview** constructs and validates a candidate DHE model, outlines its targets on the current map, and displays its candidate 3D cells. **Apply** accepts that preview; if none exists, it computes and validates one first. Changes to selection or parameters invalidate the preview. A failed operation leaves the current session unchanged.

The initial extrusion is Z=0–5. Each Apply advances **ΔZ=10** by default, or the positive increment entered by the user. An unchanged simple cell raises its roof and extends existing walls without adding redundant level rings or wall bands. Shared coplanar walls are combined only when interface ownership permits. Existing neighbours can receive vacated source regions or lose areas entered by a target. Empty space remains exterior; artificial background polygons are not created. Neighbour updates that produce empty, disconnected, holed, overlapping, or invalid footprints are rejected.

Point and line representations are terminal: they remain visible on later 2D maps while their solids end at the collapse height. They are not given a fabricated polygon cap or extruded into new volume.

## Continuous cells and history

The model retains the external boundary below each new interval and removes the previous horizontal roof. New upper faces are integrated into continuous closed cells; there is no hidden second cell per height interval. The updated map comes from the realized top section, not a cosmetic target overlay.

Smooth merging preserves the historical cells and ownership below the source level. Within the new interval, inclined interfaces transfer space from the losers to the winner; the occupied union stays constant. Losers remain valid historical cells, with zero-area terminal contacts, after retirement from the current top map. Further operations act on the winner's union footprint. Aggregation retains separate lower source cells connected through source-level interfaces to one new upper aggregate cell, with its existing growth surface in the hull-minus-union region.

**Undo** or the history selector restores an earlier DHE model. Applying after navigating backwards replaces the later history branch. History is in memory for this session; closing the window does not save a project file.

## DHE construction and methodological scope

The workspace reuses the paper's boundary-chain strips, collapse-strip helpers, and straight-skeleton aggregation geometry when their preconditions hold. It adds a conforming vertical-interval arrangement to support exterior space, multiple sources, and repeated levels. Genuine shared interfaces use `JoinByFace`; faces within each shell use `Sew`, following `MEVVFS`, `MEV`, and `MEF` face construction. No existing Euler operator was changed.

Temporary planar DHE triangulation is not a prerequisite in this workspace. Final transition facets are real DHE faces. Nested or complex polygon active regions that cannot use the paper's two-crossing strip are triangulated geometrically and lifted by

```
z(p) = z_source + (z_target - z_source) d_source(p) / (d_source(p) + d_target(p)),
```

where distances are to the source and target boundaries. Shared boundary contacts use the midpoint level in this extension. An interior midpoint-height vertex subdivides an otherwise flat active triangle. Internal fallback edges with both endpoints at Zs or Zt are also refined when their midpoint lies inside the active region: one shared midpoint receives the same distance-based Z assignment, and both incident triangles are split consistently. This prevents an internal diagonal from touching the interior of a terminal cap. Existing boundary vertices and their elevations are preserved; polygon caps and non-active regions remain untriangulated. The UI reports when this additional construction is used; it is **not a claim that the original paper workflow implemented this formula**. Ridge strips are also extended to exclude zero-projection moves that would leave folded flaps instead of the requested final contact.

The reported merge **12, 14**, with winner **14**, previously produced an inclined triangle whose top edge crossed the interior of the Zt cap. The intersection validator correctly rejected that shell. Refining its fallback interior edges resolves this construction without relaxing intersection or manifold checks. Regression coverage includes both winners at source levels 5, 15 and 25 with increments 1, 10 and 20, plus a portable concave-donor example. The existing boundary elevation policy is retained; growth can begin above Zs when all relevant boundary contacts use intermediate levels.

The integration builds checked, oriented shells before allocating replacement DHE records. Checks include edge pairing, vertex links, genus-zero topology, planarity, positive volume, reciprocal dual/primal links, actual source/target sections, actual collapsed contacts, and sampled intermediate overlap checks. These are numerical checks, not a general proof of self-intersection freedom for arbitrary inputs.

Complex cases can still fail the conforming-shell checks even when the input polygon itself is valid. Such cases are reported as unsupported by this implementation; no substitute geometry is accepted. In particular, branched skeletons, non-star-shaped point collapses, split/holed neighbour results, and incompatible batches require different selections, parameters, or further algorithm development.

The original workspace audit is retained in `artifacts/generalization/default_dataset_matrix.json`. Updated before/after results and all six Feature 3 tolerance cases are produced with `audit_transition_manifolds.py --all-features` under `artifacts/manifold/`. Rejections remain explicit and transactional; outcome counts describe the tested parameters, not a guarantee for other selections or later levels. See `TRANSITION_MANIFOLD_NOTES.md` for the root-cause matrix, the triangulation-helper assessment, and methodological wording.

## Manual quantitative evaluation

**Apply does not run quantitative analysis, open an evaluation window, or save reports.** Mandatory construction validation remains in place. Click **Quantitative Evaluation**, choose **Last operation** or **Whole history**, and click **Evaluate**. The request ends at the currently displayed history state; after Undo it excludes later stored states. Opening the window, changing modes, Preview and History navigation do not calculate results.

Tables report actual source/result area and perimeter, signed and percentage changes, sampled Hausdorff distance and normalized HD. Point/polyline targets receive explicit N/A polygon metrics; mixed-map totals describe polygonal coverage, with symbol counts/lengths separate. Last operation adds one relevant descriptor per case. Whole history uses common metrics for the entire map and the changed-lineage subset, including affected neighbours.

Each operation has **11 actual DHE horizontal sections**, t=0,0.1,…,1. Whole history retains 11 per operation; use the scope and step selectors to limit displayed rows. Its reference is the initial top representation; Last operation references the preceding source. HD samples use maximum spacing D/1000, compare to original segments/points, and report D, spacing, sample counts and the sampling-only bound Hh ≤ H ≤ Hh+h/2. A sample-budget overflow gives N/A rather than silently coarsening. Counts distinguish the retained global LTS from the complete cells supporting the actual top map. Missing logical terminal symbols in later physical sections are flagged, not synthesized.

Results remain in memory until **Save results**. Browse to an output folder (default `artifacts/evaluations`), then Save writes a unique directory with `evaluation.json`, `summary.csv`, `sections.csv`, `structure.csv`, and **`analysis_report.md`**. The report explains the actual analyses, data, parameters, levels, formulas, units, normalization, values and limitations in academic English. Existing results are never overwritten. **Load saved reports** also reads legacy version-1 evaluations, clearly labelled; it does not reinterpret them as manual whole-history analyses.

Use **Plot / export profiles** for measured area/perimeter/NHD-versus-Z figures (SVG/PDF/PNG, existing Times New Roman publication settings). N/A samples create gaps. Sampled maximum and Z-weighted mean NHD are saved with coverage; heterogeneous descriptors and counts are not averaged. Construction time and memory are not estimated retrospectively. If saving fails, fix/change the folder and click Save again; the model and measured result remain unchanged. A normal close waits for an explicitly requested save. Generated reports and examples belong outside Git commits.

See [Quantitative evaluation methodology](DHE_Generalization_Evaluation_Methodology.md) for definitions and suitable journal tables/figures. Tests: `test_lts_evaluation`; native controls: `test_generalization_evaluation_ui` with `DHE_GUI_TESTS=1`. `generalization_evaluation.py` retains legacy report functions; the GUI now requests `lts_evaluation.evaluate_history` explicitly and persists only through `save_report` on Save.

## Viewing and exporting

The left map always shows the current top; the large 3D panel shows the actual DHE shell; the lower 2D panel shows the live section. The current half-edge is red and its face remains filled at 50%, including when cell filling is disabled. Face/edge buttons browse actual DHE records. **Open full DHE inspector** opens the existing navigation viewer; it closes when its model is replaced. **Last transition diagnostics…** shows the failed operation's case, parameters, cell, validation class and detailed evidence when available.

The full inspector includes **Fill all cells** (initially on when opened here). Turning it off leaves cell outlines and the independently filled current face; `x` also clears cell fills and unchecks it. `c` shades the current cell, and `l` hides the current-face highlight until the next edge selection. **Dual sizes…** opens two editable fields: marker **area** in points² (default **50**, previously 100), and dual-edge line width in points (default **2.5**, previously 5). Enter a positive finite value and press Enter to update visible symbols immediately. These settings also apply when dual visibility is toggled again and are retained when changing target type in the transition viewer. No DHE geometry or camera is modified. The programmatic viewer accepts `dual_vertex_size` and `dual_edge_width` keyword arguments.

Plane controls support Horizontal (Z), Vertical (azimuth/offset), and Inclined (tilt/azimuth/offset). Enter a positive **Step size** to control slider resolution, mouse wheel over the slider or section, ↑/↓, and Lower/Raise consistently. The 3D plane is hidden initially; interacting with the slider/keys/buttons enables it. The checkbox can hide it again. Tilt is measured from horizontal. The **3D Z exaggeration** field changes only display aspect, not coordinates or calculations.

**Export current figure…** writes PNG, SVG, or PDF at a requested DPI. Pan/zoom and camera controls are available through Matplotlib. Exports and external datasets should remain outside Git commits.

All 3D panels have white backgrounds and panes, with background coordinate grids disabled. **Show axes** controls X/Y/Z lines, names and Z-level numbers (default on). X/Y numbers are always hidden in 3D. **Show Z transition guide** displays a separate vertical numeric guide and source-to-target arrow (default off), independently of axis visibility. Only recorded levels are used, including aggregation's recorded Zm; the guide is disabled for initial extrusion, where no target transition is defined. These controls are also available in the full DHE inspector. Export current figure writes a `.json` sidecar with the displayed axes/guide settings, levels and camera.

### Focused transition viewer and paper figures

**Open transition visualization…** opens a separate read-only window for a validated preview or an applied operation. The operation dropdown browses captured history without changing the active DHE session. Initial extrusion alone has no transition to display.

- **Features:** latest affected features (selected sources, changed neighbours, and contributing earlier owners) or all features. Hide individual features using the list, or restore them with **Show all eligible features**.
- **Vertical extent:** latest interval **Zb–Zt**, including the actual preceding band below **Zs**, or **Total history**. For the first operation Zb is the initial bottom; for later operations it defaults to the previous operation's source level. An editable Zb may lie between the initial bottom and Zs. Earlier inclined geometry is preserved.
- **Figure:** a large 3D view with a live section, a standalone 3D figure, a 2D section grid, or a 3D slicing grid. Rotate/zoom with Matplotlib; explicit camera, opacity, exaggeration and outline settings affect only the illustration.
- **Fill cells in 3D:** turn this off for coloured wireframe boundaries, including unfilled display cut caps. Extracted sections and the purple slicing plane retain their own colours.
- **Slicing:** horizontal Z, vertical azimuth/offset, and inclined tilt/azimuth/offset; the step field controls the slider, wheel, arrows and Lower/Raise. The plane starts hidden. The section remains visible in the linked view. Purple planes and optional closing surfaces at display cuts are visualization aids, not new DHE facets.

The **6** and **9** slice options produce 2×3 and 3×3 panels. **Start Z** and **End Z** select an inclusive, evenly sampled range; click **Update slice grid** after editing. **Use Zs–Zt** restores the current operation's transition interval, also restored when choosing another operation. Blank endpoints use the corresponding Zs or Zt default. The range must satisfy displayed Zb ≤ Start Z < End Z ≤ Zt. Choose **Total history** to include earlier levels; invalid ranges are reported without replacing the last valid figure. These fields do not move the live slicing slider. Actual point, polyline and retired-cell contacts remain lower-dimensional at the top. Colours, extents and camera are consistent across a grid. Use the three export buttons for a clean 3D figure, a 2D grid or a 3D grid; select **Also export individual slice panels** when needed.

Grid previews, combined exports and individual panels use exactly the same sampled levels and panel letters. JSON records `slice_range`, `slice_levels`, and `view.slice_z_start` / `view.slice_z_end`. Custom-range filenames include both endpoints; default-range filenames retain their previous form. For headless use, pass `ViewSettings(slice_z_start=17, slice_z_end=23)` (with levels inside the displayed extent) to `render_slices` or `export_transition`. Scene-only exports do not use the grid range.

**Show 2D slice X/Y numbers** is off by default. 2D grid figures omit X/Y coordinate numbers and names; the linked live section hides the numbers but retains its coordinate names. The checkbox restores numbers in 2D sections only. 3D panels show X/Y/Z names and only transition Z numbers when axes are on. Panel letters and section-level titles remain visible when axes are off.

**Horizontal grid gap** and **Vertical grid gap** default to 0.05 and 0.10, expressed as fractions of subplot width/height. Click **Update slice grid spacing** after editing them. Zero is supported; there is no forced minimum gap. These grid-specific values override the generic publication-layout spacing for slice figures and are saved in the export's `view` settings (`slice_wspace`, `slice_hspace`). Equal geometric aspect ratios are retained; adjust overall width/height as well for unusually narrow or wide shapes. `fill_cells` and `slice_xy_numbers` are also available through `ViewSettings` for headless exports.

The viewer balances the displayed Z length against the XY domain because Z is a transition coordinate. **Z display factor** scales that display length; it never changes vertex coordinates. Use **Update view / camera** after editing the camera, Zb or zoom fields. Mouse-adjusted 3D axis limits and camera are preserved during slicing and export; changing the operation, feature scope or vertical extent fits the new domain again.

Every export writes **SVG, PDF and PNG**, plus JSON containing operation levels, visible owners, camera, publication settings and export outcomes. SVG keeps text editable and PDF embeds TrueType fonts; geometry stays vector. Times New Roman is required, and its absence is reported. Width/height in inches and DPI are editable; pixel width/height override physical sizing when entered. DPI has no arbitrary low maximum: 300, 600, 1200 and larger values are accepted, with raster-memory/backend safeguards. If a PNG is too large, SVG/PDF remain available and the reason is reported.

**Fonts / publication settings…** exposes the existing validated publication JSON. Adjust independent title, panel-title, axis-label, tick and legend font sizes, layout spacing, margins and raster-memory budget there. Set physical size/DPI/pixel dimensions in the main controls or in that dialog. Exports preserve the chosen scene camera and visibility. Generated assets belong under ignored `artifacts/` or an external output folder.

For headless 3D rendering, `ViewSettings(show_axes=False, show_z_transition=True)` keeps the numeric Z guide while hiding coordinate axes. Both flags and the actual levels are retained in export metadata. `render_overview` accepts the same keyword flags. The guide does not add geometry or allocate DHE records.

With the guide enabled, numeric Z values appear on the guide rather than being duplicated beside the native Z axis. Closely spaced levels use offset labels with leaders; native axis lines and tick marks remain available through **Show axes**.

Headless publication export from an existing session:

```python
from generalization_visualization import TransitionSnapshot, ViewSettings, export_transition
from methodology_publication import PublicationSettings

snapshot = TransitionSnapshot.capture(session.state, session.history[:session.cursor])
settings = PublicationSettings({"layout": {"width_in": 10, "height_in": 8},
                                "export": {"dpi": 600}})
export_transition(snapshot, "artifacts/transition_figures", settings=settings)
export_transition(snapshot, "artifacts/transition_figures", content="slices",
                  count=9, kind="2d", individual=True, settings=settings)
export_transition(snapshot, "artifacts/transition_figures", content="slices",
                  count=6, kind="3d", options=ViewSettings(extent="history"), settings=settings)
```

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
.\.venv\Scripts\python.exe -m unittest test_generalization_visualization -v
.\.venv\Scripts\python.exe -m unittest test_aggregation_conformity -v
.\.venv\Scripts\python.exe -B -m unittest test_merge_terminal_edges -v
.\.venv\Scripts\python.exe -B -m unittest test_input_overlap_controls -v
```

The real-dataset test runs when the local default Shapefile exists; portable tests use synthetic geometries with the production builders. Live Overpass availability is separate from parser/geometry correctness.

See [Aggregation conformity notes](AGGREGATION_CONFORMITY_NOTES.md) for shared skeleton-event timing, the reported aggregation selections, local bounds for verified near-collinear backend rays, numerical column artifacts and independent reference-section checks. The six-feature selection **17, 5, 7, 10, 27, 30** is covered at source levels 5, 15 and 25 with increments 1, 10 and 20; unexplained timing conflicts remain explicit failures.
