# DHE Transition Atlas · local web workspace

This browser application uses the existing Python generalization engine and actual DHE cells. It is independent of `main.py` and the desktop defaults. It provides interactive map/3D views rather than publication exports or quantitative evaluation.

## Run

Install the optional web dependencies into the project environment once:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-web.txt
.\.venv\Scripts\python.exe web_generalization_app.py
```

The script prefers **http://127.0.0.1:8766**, reserves an available port if that address is occupied, and opens its actual printed URL only after this server starts successfully. Another project already running on port 8765 is unaffected. Keep its Python process running; Ctrl+C stops it. `--no-browser` suppresses browser launch, `--port NUMBER` requests a fixed port (an occupied one fails without launching the browser), and `--initial-height 5` sets the initial extrusion. `--input-shp PATH` overrides the default `C:/University/PhD/Data/Kleczkow/osm_data4.shp`.

The requested default file contains 32 supported polygons and builds 32 initial DHE cells at Z=0–5. It is external data and is not included in Git. If it is unavailable on another computer, use Load data. The server binds only to loopback. It has one in-memory workspace: tabs share the same state, page refresh retains it, and stopping the server ends the session. This is not a public hosting configuration.

MapLibre GL JS **6.16.0** and deck.gl **9.4.1** are loaded as pinned browser ES modules from esm.sh; the map stylesheet is loaded from unpkg. A browser with WebGL2 and Internet access is required. Background service errors preserve the DHE scene; choose another background or White / no background. A browser-module loading failure is reported in the bottom status bar.

## Load polygons

**Load data** opens the default dataset or uploads GeoJSON (`.json`/`.geojson`), GeoPackage (`.gpkg`), a ZIP containing a Shapefile, or directly selected Shapefile companions. For direct selection, select one `.shp`, its matching `.shx` and `.dbf`, and optionally `.prj`/`.cpg` together. A lone `.shp` cannot supply attributes and indexing; missing companions are reported explicitly. Pick a layer when several are available. The maximum combined upload is 100 MiB; archives are limited to 500 entries and 512 MiB expanded. Unsafe archive paths and symbolic links are rejected. Uploads are temporary, local, and cleaned up when the server stops; at most ten successful uploads are staged per server session.

The web adapter preserves an existing metre CRS and projects geographic/other-unit data to local UTM. For data without a CRS, enter the original coordinate system, such as `EPSG:2180`; it is not guessed. MultiPolygons are split into polygon components before overlap processing.

Upload preflight displays the declared CRS, raw bounds and input count. Projected datasets with longitude/latitude-sized coordinates require an explicit choice: **Use declared CRS** (appropriate for legitimate small local coordinates), or **Override declared CRS** and enter the true coordinate interpretation. The suggested EPSG:4326 is advisory, never applied automatically. An override assigns the interpretation to the existing coordinates **before** projection and is recorded in import notes. It does not reproject from the incorrect declaration or modify the source files. The older `crs` API field still only supplies a missing CRS; `override_crs` explicitly replaces a declaration, and `accept_declared_crs` acknowledges the warning.

For the investigated `C:/University/PhD/Data/Kleczkow/osm_data2.shp`, choose **Override declared CRS → EPSG:4326**. It declares EPSG:3857 but has raw bounds approximately 17.010–17.051° E and 51.125–51.135° N; shared OSM features match the default dataset in these raw coordinates. Its 674 input polygons prepare into 669 supported footprints after automatic overlap unions. This is an import interpretation correction, not a change to DHE construction.

Loading reports real stages: reading/CRS/overlap preparation (or OSM download), DHE construction/validation, then geographic scene preparation. Prepared input footprints appear promptly as **Input preview — constructing DHE**; they are not presented as completed DHE cells. The previous applied model and history are retained until the replacement succeeds. Large datasets can take several minutes in the existing engine; no percentage or synthetic geometry is used. Job failures remain visible after a refresh even if a previous dataset is loaded.

**Positive-area overlap groups are automatically unioned**, including transitive chains. If A overlaps B and B overlaps C, their union becomes one input feature. Merely touching or separate features remain distinct. Attributes come from the largest member, with input order breaking ties. Every member's input row, component, original identifier, and attributes remain in `source_members`; stable workspace indices use the representative input index. Dataset notes disclose these unions. Source files are never edited. This import policy is separate from the desktop overlap-review policy.

Invalid/non-polygonal records and unions that remain holed are reported and excluded. Holes are not filled artificially, and no supported inputs produces an explicit load error. A failed replacement leaves the previous dataset and history in place.

**Draw OSM area** switches the main map temporarily to top-down rectangle drawing. Draw the area, then click **Download footprints** in the banner. Esc cancels. The existing Overpass adapter retrieves complete building footprints, including boundary nodes outside the rectangle. OSM parsing limitations and service failures are reported. Footprints are prepared with the same web union policy; they are not clipped to the rectangle. Background imagery is not used to extract geometry.

Network failures try the Private.coffee public Overpass instance after the default overpass-api.de endpoint. Geometry/parameter errors are not retried as network errors. If both services fail, the current workspace remains available. Public instance addresses are listed in the [OSM Overpass documentation](https://wiki.openstreetmap.org/wiki/Overpass_API).

## Generalize and inspect

The **Level operations · one ΔZ** table supports independent operations in one source–target interval. Add each selection with its parameters, edit/remove rows, then Preview/Apply the complete level. Duplicate source assignments are rejected by the client and server. Emptying the table restores the existing single-operation behavior.

**Automatic…** builds a complete selected-family consolidation in one background job and displays only its final result. Starting scale defaults to 1:10,000, with editable scale multiplier, ΔZ, final geometry, optional class constraint, thresholds and limits. The selected family becomes one logical feature; its historical DHE cells remain. Neighbours can receive normal geometry updates. Cancel, validation failure, or an unfinished run keeps the original scene and revision. Successful runs publish once and expose every new level in History. **Run summary** explains selected actions and provides a JSON download. See [selection rules and methodology](AUTOMATIC_GENERALIZATION.md).

Select current polygons using the feature list, indices such as `1, 3-6`, or the **Current top** map. Click selects one; Shift-click adds/removes features. List checkboxes toggle individual features. **Box select** expands the current-top map for rectangle selection. The selection map always shows the active top representation, even while the main view slices earlier levels.

Choose Simplification, Smoothing, Collapse to point, Collapse to polyline, Merging, or Aggregation. Tolerance, smoothing iterations/ratio, merge winner, and top-up ΔZ use the existing engine. Initial height defaults to **5**, and each operation adds **10** transition units unless changed. Preview constructs and validates a temporary model; Apply commits the matching preview or computes a new one. Changing the selection/parameters discards the displayed preview. Undo and History restore earlier states; applying there replaces the later branch.

Existing source–target construction, neighbour updates, unchanged-cell roof lifting, terminal collapses, and many-to-one aggregation topology are preserved. Generalization acts on the current top; moving a viewing plane never changes the operation's source level. Unsupported operations fail explicitly and preserve the applied state. There is no general guarantee that every valid source can undergo every operation with every parameter.

**3D DHE space** initially shows the primal cells, edges, and vertices. Add dual vertices/edges with **Dual**, or show only duality by hiding Primal. **Fill cells**, opacity, and Z display factor affect rendering only. Inspect DHE provides actual face/half-edge selection and NF, twin, and adjacent-cell navigation. A selected face receives its feature colour at 50% opacity, with a red current half-edge. Dual sizes, labels, and exterior connections are configurable. Links correspond to actual shared interfaces, including retained lower-source to upper-aggregate interfaces.

Dual vertices default to shaded, instanced **3D spheres**, with **Dots** available under Inspect DHE. The radius control specifies approximate screen pixels at map centre: the sphere uses a world-space radius updated with zoom, so perspective still applies. Sphere radii remain isotropic when the Z display factor changes; only centres follow Z exaggeration. Actual XYZ dual connections remain lines, with adjustable pixel width. One unit sphere mesh is reused for all vertices. The inspector lists cells first, then at most 300 half-edge matches. Search labels/raw IDs across the complete scene; clicking a face/edge or navigating NF/twin/adjacency can still reach every stored edge. No model elements are removed to reduce menu size.

The web view georeferences local XY back to longitude/latitude for alignment with the background. Raw Z is a **transition coordinate**, not measured building height. Rendering exaggeration does not edit coordinates. Concave and vertical faces are tessellated in their own planes for GPU display; artificial tessellation edges are not drawn or added to DHE topology.

## Slice the actual space

The bottom bar supports Horizontal (Z), Vertical (azimuth/offset), and Inclined (tilt/azimuth/offset). Its slider, editable value, Lower/Raise buttons, ↑/↓, and mouse wheel over the slider use the editable positive step. The source/target shortcuts select the latest Zs/Zt. Slider bounds span the stored complex; angular planes use their actual offset bounds.

With **3D on**, coloured sections lie on the actual cutting plane. With **3D off**, primal/dual geometry is hidden and sections are projected onto the XY background in a top-down map; switching back restores the 3D camera. A vertical-plane projection can be a line rather than a filled polygon. A small display-only ground offset avoids depth fighting. Section polygons, holes, lines, and points are reconstructed by `PlaneSlicer`; numerical interpolation between endpoints is not used.

**Show slice features** defaults on and independently controls section fills, outlines and points in the main view. **Show slicing plane (3D)** defaults off and independently controls the translucent plane and its border. All four on/off combinations are supported. The plane is hidden in projected/2D mode while its preference is remembered; the current-top selection map stays available. Hiding sections skips their reconstruction (`include_sections=false`), returning only plane bounds/outline, and the status explicitly says they were not calculated. Orientation, position, step and visibility choices persist across operations/replacement datasets; positions are clamped to valid new bounds. Slider requests use one in-flight calculation and retain the latest queued position.

Point/polyline targets are terminal in the existing engine. A logical symbol may persist in the current-top map after its DHE cell ends. Missing terminal features at a later actual top section are disclosed; no fabricated solid or section geometry is inserted.

## Mapping two independent layers

**Mapping** has its own inputs, revisions, preview and published model. Generalize
and its history remain available when switching back. Both workspaces use the same
single construction worker, preventing concurrent changes to DHECore globals.
Mapping uses the existing map, plane controls, current-top map and Inspect DHE.

1. Open the default source and target, or upload each separately. Defaults are
   `C:/University/PhD/Data/Kleczkow Updated/osm_data.shp` and
   `C:/University/PhD/Data/Kleczkow Updated/citygml_data.shp`. Override them with
   `--mapping-source PATH --mapping-target PATH`. Sources must be polygonal; targets
   can also be points or simple open lines. Upload formats and CRS preflight match
   Generalize. A common metre CRS and XY origin are used for construction.
2. Positive-area overlap chains **within each layer are automatically unioned**.
   Touching neighbours stay separate. The largest member supplies the retained
   identifier/attributes, with input order breaking ties. Every member's original
   identifier and attributes remain in provenance. Unions and per-feature polygon
   repairs affect in-memory copies only and are listed in the interface/report.
3. Set the mapping path and source/target Z (defaults 5 and 15). Optional scale
   denominators, dates and source labels are report metadata. Z represents the
   transition dimension, not building height; the lower datum is zero.
4. Correspondences are reviewed automatically once both layers load. Polygon links
   use intersection area divided by the smaller area (default minimum 25%);
   contained target symbols also qualify. Connected strong links are accepted.
   Unmatched sources automatically disappear and unmatched targets appear.
   Proximity candidates within 10 m remain suggestions and are never accepted
   based on distance alone. **Edit auto review** optionally corrects groups using
   the `s…` and `t…` IDs, excludes features or sets a cyclic vertex anchor.
   Confirmation checkboxes are informational; there is no confirmation gate.
   Input or matching-threshold changes recompute groups; otherwise valid manual
   corrections are preserved. **Refresh auto matches** explicitly recomputes them.
5. **Preview** constructs and validates a temporary DHE snapshot. **Build mapping**
   publishes a validated result. Both complete automatic preparation and save any
   optional review edits first. Failed or cancelled jobs retain the previous published
   model. Cancellation is checked between planning groups and construction stages;
   an active core construction finishes before cancellation takes effect.
6. Use the source/target slice shortcuts, vertical/inclined planes and Inspect DHE.
   **Download report** includes the construction's saved review, routing reasons,
   settings, repairs, original identities, attributes, members and endpoint checks.
   Later review edits mark the displayed built result as stale without rewriting
   its report. This is an in-memory session; saving a report does not persist a
   reloadable model.

| Path | Construction |
| --- | --- |
| Classic | Source cells extend from Z=0 to the midpoint of source/target Z; supplied target cells extend from that interface to target Z. Coplanar cap overlays are partitioned into common faces and joined using actual DHE `JoinByFace` topology. No deep merge erases the two layer identities. |
| Smooth | One-to-one rigid displacement/orientation connects cyclically corresponding vertices, with automatic rigid-fit alignment. Other polygon changes use the existing polygon transition. Point and line collapses use the supplied target, subject to the existing visibility/ridge conditions. Unmatched features use generated centroid transitions as described below. Separated-source aggregation first reaches the convex hull, then transitions to the supplied target in the same upper cell; the target is not replaced by that hull. |
| Hybrid | Groups containing polygon holes, adjacent-source merging (including displacement plus merging), splits and many-to-many groups use Classic. Other supported one-to-one changes/collapses, unmatched centroid transitions and separated-source aggregation first use Smooth; groups implicated in diagnosed topology failures retry through Classic. The review/report records the initial and final builders and each retry reason. |

**Map only by ID** is off by default. Enable it in the Mapping tab to match every
polygon by its displayed original ID in one batch, regardless of location or
overlap. For example, a source `osm_id` matches a target `gml_id` only when their
string values are equal. Comparisons preserve case, spaces, and leading zeros;
blank or missing supplied IDs never match generated row numbers. Repeated IDs
form one group containing all source and target polygons with that ID. IDs on
only one side disappear or appear, and point/line targets remain appearances.
The existing Classic/Smooth/Hybrid construction rules still apply to each group.

Changing the checkbox refreshes all matches and clears previews; an existing
published result stays available and is marked stale. The option can be set
before importing either layer. While enabled, spatial matching settings and
manual group edits are disabled. Overlapping polygons with the same supplied ID
are unioned automatically; overlaps between different IDs must be assigned or
excluded using the overlap controls before building. Cross-ID unions are
disabled. Original inputs are retained before automatic unions, so turning the
option off restores spatial matching and turning it back on recovers those IDs.
Explicit overlap resolutions are retained. The boolean `match_by_id` setting is
included in Mapping state and construction/export reports.

In Smooth and Hybrid, a group without a target disappears by uniformly scaling
its polygon to its area centroid between source and target Z. A group without a
source appears by reversing that transition. Concave polygons use their true
centroid even when it lies outside the footprint. These generated transitions do
not change the visibility restrictions for explicitly supplied point targets.
For multiple polygon parts in one unmatched group, disappearance uses Classic
construction up to the Z midpoint, joins the source caps to one convex-hull
cell, then smoothly collapses that hull to its centroid. Appearance reverses the
sequence: centroid to hull, then Classic construction to the original target
parts. Intermediate hulls retain member provenance and are not output features.
An unmatched target line expands from its length-weighted centroid through real
triangular DHE sheets; an unmatched point uses a vertical edge trajectory.
Reports record transition direction, projected centroid coordinates and CRS, and
the midpoint stage level when used. Both input layers are still required;
polygons with holes keep their existing Classic/Hybrid handling and Smooth
limitation.

Hybrid collects independent shell failures before allocating DHE records and
rebuilds the implicated Smooth groups through Classic, then reconforms and validates
the complete combined complex. Each group changes to Classic at most once;
later interface/intersection failures can identify additional Smooth groups.
Independent intersection failures are collected together. During retries, a cell's
successful geometry checks are reused only when its exact coordinates, winding
and face labels are unchanged; combined interfaces and live DHE topology are
validated again on every attempt.
Only attributed topology failures qualify. Classic-only failures, unidentifiable
errors, invalid parameters, unexpected allocation errors and cancellation are not
hidden by retries. Preview/build remains atomic, and supplied endpoints are retained.
Progress names groups being retried; after success the review table shows actual
final routing. Reports include `initial_builder`, `fallback`, `construction_attempts`
and `retry_history`, while failed-job diagnostics identify layer, feature, original
identifier, group, cell and stage. Smooth-only remains strict.

Vertical-wall overlay uses a horizontal wall axis and an exact Z axis with zero
Z origin. Assigned levels survive projection/reconstruction without becoming
values such as `9.999999999999998` instead of `10`. This does not change geometric
modelling tolerances or merge distinct semantic levels.

Classic point targets are actual two-half-edge vertical DHE edge primitives;
Classic line targets are actual two-sided vertical DHE sheets, one per line
segment. Their shared primal vertices and edge/vertex contact registry are
validated separately from positive-volume solid cells. **Edge / vertex contact**
in Inspect DHE follows these reciprocal incidences; ordinary shared-face
navigation retains its existing meaning. Dual links identify face, edge or vertex
incidence. A semantic correspondence can exist without geometric contact at the
Classic interface; the report does not invent adjacency for displaced footprints.

Classic automatically decomposes holed polygon components into constrained
triangles and constructs their shared DHE interfaces. Holes remain empty; their
area is never filled. A decomposed feature retains one logical identity and review
row while containing several physical DHE cells. Reports distinguish logical
source/target counts from physical cells and record decomposition/hole areas.
Hybrid selects Classic for these groups. Smooth-only explicitly reports unsupported
holes, splits, many-to-many groups, unmatched features or invalid geometry; it
never silently changes to Classic. MultiPolygon identities retain their components
as separate cells in Classic; Smooth requires one target polygon.
Intersecting transition shells are rejected, even when their endpoint polygons are
individually valid. Arithmetic conformity is distinct from the editable rigid-fit
tolerance. Actual source-Z and target-Z sections are compared with the approved
input footprints before publication.

The supplied defaults contain 674 source and 431 target records. The target export
has coincident polygon components requiring per-feature repair. There are five
source and three target cross-feature overlap pairs. Automatic unions prepare
**669 logical source features and 428 logical target features**. Unioning `t47`
and `t157` leaves a genuine enclosed gap of approximately **0.000272 m²**; it is
preserved through decomposition instead of blocking construction. The original
Shapefiles are never changed.

The full-data regression exercises automatic unions, matches and unmatched
classifications without supplying manual decisions, then checks actual source-Z
and target-Z sections against the prepared inputs. Input-file hashes are checked
before/after construction. The client regression also exercises direct preview
after loading and optional subsequent corrections using a mock DOM/WebGL adapter.
The latest complete rechecks preserved all 669 source and 428 target logical
features, including the hole in `t47`, and verified unchanged input-file hashes:

| Path | Physical cells | Shared faces | Directed half-edges |
| --- | ---: | ---: | ---: |
| Classic | 1,191 | 1,850 | 112,510 |
| Hybrid | 1,191 | 1,847 | 117,706 |

Hybrid completed in five construction attempts: 216 initially Smooth groups
retried through Classic, leaving 140 Smooth and 468 Classic groups. Target `t339`
(the former Feature 21 error) ultimately uses Classic because its Smooth shell
intersects another target's interface; its original Z mismatch is corrected too.
The Classic and Hybrid rechecks took about 7.8 and 12.9 minutes respectively on
the test machine; concurrent processes affect timing. All endpoint checks passed.
The broader Python regression suite passed 95 tests, and the real-API client test
exercises Hybrid collision retries and final routing with a mock DOM/WebGL adapter.
Browser appearance remains a separate check.

## Space exports and cell focus

The shared **Export space** section works in Generalize and Mapping. It exports
the displayed validated model or preview, including retained lower/history cells.
Downloads include all bounded physical cells and primitives regardless of layer
visibility, selected features, slice position, or the display Z factor. A
decomposed logical feature keeps its separate physical cells and provenance.
Exports are disabled until a validated snapshot exists and during construction.

**Primal OBJ** downloads a ZIP containing `primal.obj`, `primal.mtl` and
`metadata.json`. Each bounded cell is a separate OBJ object with its cell colour.
Faces retain their original DHE polygon cycles, including concave polygons;
display triangles are not exported. Internal interfaces occur once per incident
cell with opposite winding. Mapping sheets retain both sides and edge primitives
use OBJ `l` elements; no thickness or exterior volume is invented. The metadata
contains cell identities, attributes, members, lineage, face uses and an
`obj_vertex_ids` table: its first entry maps OBJ vertex 1 to the corresponding
vertex ID in the full DHE export. `obj_element` numbers the `f`/`l`/`p` elements
together, starting at 1. Polygon support and Z-up import settings depend on the
receiving OBJ application.

**Duality JSON** contains schema-version-1 tables:

| Table | Contents |
| --- | --- |
| `vertices` | Snapshot-scoped ID, original raw ID, XYZ and primal/dual/exterior-placeholder kind |
| `dhe_records` | All live bounded, exterior and dual half-edges, with `V`, `S`, `D`, `NF`, `NV`, labels, raw IDs and optional display-half-edge ID |
| `face_uses` | Owning cell, seed record, face part and ordered primal vertex IDs |
| `cells` | Physical cell ID, seed, dual vertex, dimension, colour and source provenance |
| `dual_graph` | Cell nodes and actual face adjacency; typed Mapping edge/vertex contacts are separate `mapping_contact` relations |

IDs are scoped to one snapshot and reference actual records. Coincident but
distinct topology records remain distinct. `mapping_incident` lists live contact
record IDs. VINF is an unbounded exterior node with a null graph position; its
stored vertex coordinates are explicitly marked as placeholders. Legacy scratch
fields are not structural pointers and are not exported.

The coordinate selector defaults to **Local XY (metres)**. **Absolute projected
CRS** adds the model's XY origin. Both preserve raw transition Z, use Z-up axes,
and include CRS/WKT, origin, coordinate mode and Z semantics. Z represents the
transition dimension, rather than measured building height. No display
exaggeration or geographic longitude/latitude conversion is applied.

Read-only downloads use `/api/export/primal` or `/api/export/duality` with required
`workspace_name=generalize|mapping` and `snapshot_id` query parameters, plus
optional `coordinates=local|projected`. Compact immutable tables are captured
when the scene is prepared. Downloads pin those tables without rebuilding DHE
or consulting its mutable global registry. Expired/replaced snapshot tokens return
HTTP 409. A retained Mapping result from an earlier review remains exportable
with its original report and explicit `stale` metadata.

In **Inspect DHE**, select a half-edge and click **Zoom to current cell**. The main
map fits its actual owning physical cell in XYZ, accounting for the current Z
factor or flat mode, while preserving pitch, bearing, highlighted edge and other
view settings. Small point/line footprints receive a little map context. The
current-top mini-map retains its context. The button is disabled without a valid
edge; decomposed features focus the selected physical cell.

Validation includes 104 Python regressions, real-API client control/download
checks in both workspaces, and 90 camera fits using the actual deck.gl projection
at multiple pitches, bearings, viewport sizes and cell shapes. These are numeric
and control checks; browser appearance remains a separate check. A full default
Classic build exported all 1,191 cells, 393,236 live DHE records, 28,143 vertices
and 19,866 face uses. Its JSON was 75.4 MB and its OBJ ZIP 1.1 MB; all structural
references and endpoints passed, and input-file hashes remained unchanged.

## Backgrounds

OpenStreetMap is the default. Light/dark OpenFreeMap styles, Esri World Imagery, White / no background, and a custom Web Mercator XYZ tile URL are available. Custom services need `{z}`, `{x}`, `{y}`, browser-accessible HTTP(S), and attribution. Visible provider/data attribution is retained.

Background warnings name the map/provider/source and expose **Retry background** and **White background** controls. A remote style failure before initialization switches the affected map to a plain background so the geometry can initialize. Successful background source loading clears a stale warning. DHE layer/WebGL errors use a separate renderer message, and import/OSM/construction errors appear in job status rather than being labelled background failures. A white background still requires WebGL and the browser modules themselves to load. Both maps refit after a dataset replacement; a hidden current-top map defers its fit until reopened.

Optional **Google Maps road/satellite** uses the official Map Tiles API through the local server. Set `GOOGLE_MAPS_API_KEY` in the launching process with Map Tiles API enabled, then restart:

```powershell
$env:GOOGLE_MAPS_API_KEY = 'YOUR_OWN_KEY'
.\.venv\Scripts\python.exe web_generalization_app.py
```

The key is never committed, returned by `/api/config`, or inserted in browser tile URLs. Configure restrictions appropriate for server-side Map Tiles requests. Session expiry, tile cache headers and viewport attribution are handled by the adapter. Without a key the Google choices are disabled and other providers work normally. See [Google setup](https://developers.google.com/maps/documentation/tile/get-api-key), [session tokens](https://developers.google.com/maps/documentation/tile/session_tokens), and [attribution policies](https://developers.google.com/maps/documentation/tile/policies).

## Architecture and validation

`web_generalization_app.py` serves the local assets and job-based JSON API. Model mutations run on one executor thread because DHECore has global registries. Revision checks reject stale requests and concurrent construction jobs. Scene snapshots and their slicers are read-only; rapid slider responses are debounced and older responses discarded. Other tabs periodically synchronize to the applied revision. No figure generation, automatic quantitative analysis, or project persistence is added.

The import and scene adapters are `web_generalization_data.py` and `web_generalization_scene.py`. Application assets are under `web_generalization/`. API endpoints include `/api/state`, `/api/scene`, `/api/jobs/{id}`, `/api/uploads`, `/api/load-default`, `/api/load-upload`, `/api/osm`, `/api/preview`, `/api/apply`, `/api/history`, `/api/slice`, and `/api/select-box`.

The shared construction engine now uses `SewComplex` to compose internal and exterior `Sew` pairings for every within-cell seam. Its strengthened model validator audits live exterior and dual links as well as bounded-cell topology. Shared interfaces continue to use the specialized `JoinByFace` sequence; aggregation retains distinct lower source dual vertices. This does not change scene/API schemas, geometry, or bounded-cell counts. Restart the server and reload the dataset to replace models constructed by an older running process; existing in-memory history is not silently changed.

Run the portable integration tests:

```powershell
.\.venv\Scripts\python.exe -B -m unittest test_web_launch test_web_generalization test_mapping test_space_export test_transition_conformity.WallGroupingTests -v
node --check web_generalization/app.js
node --check web_generalization/mapping.js
```

With a local server already running, `node --experimental-vm-modules test_web_generalization_client.mjs` checks client control/data flow, actual downloads and cell-focus wiring against the API using a mock DOM/WebGL adapter. Set `DHE_WEB_TEST_URL` to change the test URL. This is not a visual rendering test, and Node is not required to run the application.

The separate numeric camera regression uses the browser's pinned deck.gl version.
Install its test-only dependency in the ignored artifacts directory, then run:

```powershell
npm install --prefix artifacts/camera-test --no-save --ignore-scripts --no-audit --no-fund '@deck.gl/core@9.4.1'
node test_cell_focus.mjs
```

On an isolated QA server with small disposable source/target defaults, set
`DHE_WEB_TEST_MAPPING=1` to also exercise Mapping loading, review/regrouping,
Classic preview/build, slicing, inspector metadata and return to Generalize.
This test changes that QA server's in-memory review and published Mapping model.

Set `DHE_WEB_STRESS_INSPECTOR=1` for the client test to repeat actual edge records in its mock scene, exercising a menu with over 50,000 candidates. This is only a menu stress test; server topology and geometry are unchanged.

Tests use actual operations and topology, compare all three plane orientations with the live slicer, check coordinate alignment, import unions, upload layers/archives, preview reuse, history, failures, and stale requests. Google sessions/tiles/attribution are tested with mocked provider responses when no key is configured. Live provider availability and browser/WebGL verification are separate from these API checks.

The import/control update was checked with a 50-test Python regression suite, followed by 17 final web/API tests and client control tests. A complete direct-companion import of `osm_data2.shp`, with the confirmed EPSG:4326 override, built and validated **669 cells, 46,290 directed half-edges and 27,026 display triangles** at the correct geographic location. Construction plus scene preparation took about **9.9 minutes** on the test machine (other processes affect timing); the prepared footprint preview was available before cell construction. A live OSM request timed out on both configured services and correctly preserved the previous 32-cell workspace. Provider success/fallback paths are also tested with controlled responses. Real browser/WebGL appearance could not be visually verified because the browser access host was unavailable; client tests use a mock renderer.
