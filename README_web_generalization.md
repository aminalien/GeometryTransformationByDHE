# DHE Transition Atlas · local web workspace

This browser application uses the existing Python generalization engine and actual DHE cells. It is independent of `main.py` and the desktop defaults. It provides interactive map/3D views rather than publication exports or quantitative evaluation.

## Run

Install the optional web dependencies into the project environment once:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-web.txt
.\.venv\Scripts\python.exe web_generalization_app.py
```

The script opens **http://127.0.0.1:8765**. Keep its Python process running; Ctrl+C stops it. `--no-browser` suppresses browser launch, `--port 8766` changes the port, and `--initial-height 5` sets the initial extrusion. `--input-shp PATH` overrides the default `C:/University/PhD/Data/Kleczkow/osm_data4.shp`.

The requested default file contains 32 supported polygons and builds 32 initial DHE cells at Z=0–5. It is external data and is not included in Git. If it is unavailable on another computer, use Load data. The server binds only to loopback. It has one in-memory workspace: tabs share the same state, page refresh retains it, and stopping the server ends the session. This is not a public hosting configuration.

MapLibre GL JS **6.16.0** and deck.gl **9.4.1** are loaded as pinned browser ES modules from esm.sh; the map stylesheet is loaded from unpkg. A browser with WebGL2 and Internet access is required. Background service errors preserve the DHE scene; choose another background or White / no background. A browser-module loading failure is reported in the bottom status bar.

## Load polygons

**Load data** opens the default dataset or uploads GeoJSON (`.json`/`.geojson`), GeoPackage (`.gpkg`), or a ZIP containing a Shapefile and its accompanying files. Pick a layer when several are available. The maximum upload is 100 MiB; archives are limited to 500 entries and 512 MiB expanded. Unsafe archive paths and symbolic links are rejected. Uploads are temporary, local, and cleaned up when the server stops; at most ten successful uploads are staged per server session.

The web adapter preserves an existing metre CRS and projects geographic/other-unit data to local UTM. For data without a CRS, enter the original coordinate system, such as `EPSG:2180`; it is not guessed. MultiPolygons are split into polygon components before overlap processing.

**Positive-area overlap groups are automatically unioned**, including transitive chains. If A overlaps B and B overlaps C, their union becomes one input feature. Merely touching or separate features remain distinct. Attributes come from the largest member, with input order breaking ties. Every member's input row, component, original identifier, and attributes remain in `source_members`; stable workspace indices use the representative input index. Dataset notes disclose these unions. Source files are never edited. This import policy is separate from the desktop overlap-review policy.

Invalid/non-polygonal records and unions that remain holed are reported and excluded. Holes are not filled artificially, and no supported inputs produces an explicit load error. A failed replacement leaves the previous dataset and history in place.

**Draw OSM area** switches the main map temporarily to top-down rectangle drawing. Draw the area, then click **Download footprints** in the banner. Esc cancels. The existing Overpass adapter retrieves complete building footprints, including boundary nodes outside the rectangle. OSM parsing limitations and service failures are reported. Footprints are prepared with the same web union policy; they are not clipped to the rectangle. Background imagery is not used to extract geometry.

Network failures try the Private.coffee public Overpass instance after the default overpass-api.de endpoint. Geometry/parameter errors are not retried as network errors. If both services fail, the current workspace remains available. Public instance addresses are listed in the [OSM Overpass documentation](https://wiki.openstreetmap.org/wiki/Overpass_API).

## Generalize and inspect

Select current polygons using the feature list, indices such as `1, 3-6`, or the **Current top** map. Click selects one; Shift-click adds/removes features. List checkboxes toggle individual features. **Box select** expands the current-top map for rectangle selection. The selection map always shows the active top representation, even while the main view slices earlier levels.

Choose Simplification, Smoothing, Collapse to point, Collapse to polyline, Merging, or Aggregation. Tolerance, smoothing iterations/ratio, merge winner, and top-up ΔZ use the existing engine. Initial height defaults to **5**, and each operation adds **10** transition units unless changed. Preview constructs and validates a temporary model; Apply commits the matching preview or computes a new one. Changing the selection/parameters discards the displayed preview. Undo and History restore earlier states; applying there replaces the later branch.

Existing source–target construction, neighbour updates, unchanged-cell roof lifting, terminal collapses, and many-to-one aggregation topology are preserved. Generalization acts on the current top; moving a viewing plane never changes the operation's source level. Unsupported operations fail explicitly and preserve the applied state. There is no general guarantee that every valid source can undergo every operation with every parameter.

**3D DHE space** initially shows the primal cells, edges, and vertices. Add dual vertices/edges with **Dual**, or show only duality by hiding Primal. **Fill cells**, opacity, and Z display factor affect rendering only. Inspect DHE provides actual face/half-edge selection and NF, twin, and adjacent-cell navigation. A selected face receives its feature colour at 50% opacity, with a red current half-edge. Dual sizes, labels, and exterior connections are configurable. Links correspond to actual shared interfaces, including retained lower-source to upper-aggregate interfaces.

The web view georeferences local XY back to longitude/latitude for alignment with the background. Raw Z is a **transition coordinate**, not measured building height. Rendering exaggeration does not edit coordinates. Concave and vertical faces are tessellated in their own planes for GPU display; artificial tessellation edges are not drawn or added to DHE topology.

## Slice the actual space

The bottom bar supports Horizontal (Z), Vertical (azimuth/offset), and Inclined (tilt/azimuth/offset). Its slider, editable value, Lower/Raise buttons, ↑/↓, and mouse wheel over the slider use the editable positive step. The source/target shortcuts select the latest Zs/Zt. Slider bounds span the stored complex; angular planes use their actual offset bounds.

With **3D on**, coloured sections lie on the actual cutting plane. With **3D off**, primal/dual geometry is hidden and sections are projected onto the XY background in a top-down map; switching back restores the 3D camera. A vertical-plane projection can be a line rather than a filled polygon. A small display-only ground offset avoids depth fighting. Section polygons, holes, lines, and points are reconstructed by `PlaneSlicer`; numerical interpolation between endpoints is not used.

Point/polyline targets are terminal in the existing engine. A logical symbol may persist in the current-top map after its DHE cell ends. Missing terminal features at a later actual top section are disclosed; no fabricated solid or section geometry is inserted.

## Backgrounds

OpenStreetMap is the default. Light/dark OpenFreeMap styles, Esri World Imagery, White / no background, and a custom Web Mercator XYZ tile URL are available. Custom services need `{z}`, `{x}`, `{y}`, browser-accessible HTTP(S), and attribution. Visible provider/data attribution is retained.

Optional **Google Maps road/satellite** uses the official Map Tiles API through the local server. Set `GOOGLE_MAPS_API_KEY` in the launching process with Map Tiles API enabled, then restart:

```powershell
$env:GOOGLE_MAPS_API_KEY = 'YOUR_OWN_KEY'
.\.venv\Scripts\python.exe web_generalization_app.py
```

The key is never committed, returned by `/api/config`, or inserted in browser tile URLs. Configure restrictions appropriate for server-side Map Tiles requests. Session expiry, tile cache headers and viewport attribution are handled by the adapter. Without a key the Google choices are disabled and other providers work normally. See [Google setup](https://developers.google.com/maps/documentation/tile/get-api-key), [session tokens](https://developers.google.com/maps/documentation/tile/session_tokens), and [attribution policies](https://developers.google.com/maps/documentation/tile/policies).

## Architecture and validation

`web_generalization_app.py` serves the local assets and job-based JSON API. Model mutations run on one executor thread because DHECore has global registries. Revision checks reject stale requests and concurrent construction jobs. Scene snapshots and their slicers are read-only; rapid slider responses are debounced and older responses discarded. Other tabs periodically synchronize to the applied revision. No figure generation, automatic quantitative analysis, or project persistence is added.

The import and scene adapters are `web_generalization_data.py` and `web_generalization_scene.py`. Application assets are under `web_generalization/`. API endpoints include `/api/state`, `/api/scene`, `/api/jobs/{id}`, `/api/uploads`, `/api/load-default`, `/api/load-upload`, `/api/osm`, `/api/preview`, `/api/apply`, `/api/history`, `/api/slice`, and `/api/select-box`.

Run the portable integration tests:

```powershell
.\.venv\Scripts\python.exe -B -m unittest test_web_generalization -v
node --check web_generalization/app.js
```

With a local server already running, `node --experimental-vm-modules test_web_generalization_client.mjs` checks client control/data flow against its actual section API using a mock DOM/WebGL adapter. Set `DHE_WEB_TEST_URL` to change the test URL. This is not a visual rendering test, and Node is not required to run the application.

Tests use actual operations and topology, compare all three plane orientations with the live slicer, check coordinate alignment, import unions, upload layers/archives, preview reuse, history, failures, and stale requests. Google sessions/tiles/attribution are tested with mocked provider responses when no key is configured. Live provider availability and browser/WebGL verification are separate from these API checks.
