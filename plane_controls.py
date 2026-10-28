"""Matplotlib controls for one live cutting plane and its separate 2D section."""

from math import copysign, floor, isfinite

import numpy as np
from matplotlib.patches import PathPatch
from matplotlib.path import Path
from matplotlib.widgets import Button, CheckButtons, Slider, TextBox
from mpl_toolkits.mplot3d.art3d import Poly3DCollection
from shapely.geometry import Polygon
from shapely.geometry.polygon import orient

from plane_slicing import PlaneSlicer, SlicePlane


class OrientationDropdown:
    """A small dropdown that works with GUI and headless Matplotlib backends."""

    OPTIONS = ("Horizontal", "Vertical", "Inclined")

    def __init__(self, fig, bounds, callback, *, options=None, value=None):
        self.canvas = fig.canvas
        self.options = tuple(options or self.OPTIONS)
        self.value = value or self.options[0]
        self.callback = callback
        self.is_open = False
        self._pressed_menu = None
        self.button = Button(fig.add_axes(bounds), self.value + "  ▼")
        self.button.label.set_fontsize(9)
        self.menu = []
        x, y, width, height = bounds
        for i, option in enumerate(self.options):
            # Opening downward keeps the target options clear of Edge ID and
            # the orientation options clear of the 3D axes and other controls.
            ax = fig.add_axes([x, y-(i+1)*height, width, height], zorder=300)
            button = Button(ax, option, color="white", hovercolor="0.88")
            button.label.set_fontsize(9)
            button.on_clicked(lambda event, value=option: self.select(value))
            self.menu.append(button)
        self._set_open(False, draw=False)
        self.button.on_clicked(self.toggle)
        self.canvas.mpl_connect("button_press_event", self._on_press)
        self.canvas.mpl_connect("button_release_event", self._on_release)
        self.canvas.mpl_connect("close_event", lambda event: self.close(draw=False))

    def _set_open(self, visible, *, draw=True):
        self.is_open = bool(visible)
        if not self.is_open:
            if self.canvas.mouse_grabber in [self.button.ax, *(b.ax for b in self.menu)]:
                self.canvas.release_mouse(self.canvas.mouse_grabber)
            self._pressed_menu = None
        for button in self.menu:
            button.set_active(self.is_open)
            button.eventson = self.is_open
            button.ax.set_visible(self.is_open)
        if draw:
            self.canvas.draw_idle()

    def close(self, *, draw=True):
        self._set_open(False, draw=draw)

    def _on_press(self, event):
        if not self.is_open:
            return
        if self.button.ax.contains(event)[0]:
            return
        self._pressed_menu = next((b for b in self.menu if b.ax.contains(event)[0]), None)
        if self._pressed_menu is None:
            self.close()

    def _on_release(self, event):
        button = self._pressed_menu
        self._pressed_menu = None
        if button is not None and not button.ax.contains(event)[0]:
            self.close()

    def toggle(self, event=None):
        self._set_open(not self.is_open)

    def select(self, value, notify=True):
        if value not in self.options:
            raise ValueError("Unknown dropdown option.")
        self.value = value
        self.button.label.set_text(value + "  ▼")
        self.close(draw=False)
        if notify:
            self.callback(value)
        self.canvas.draw_idle()


class _PositionSlider(Slider):
    def __init__(self, *args, anchor=0.0, **kwargs):
        self.anchor = anchor
        super().__init__(*args, **kwargs)

    def _stepped_value(self, value):
        if self.valstep is None:
            return value
        if value <= self.valmin:
            return self.valmin
        if value >= self.valmax:
            return self.valmax
        result = self.anchor + floor((value-self.anchor)/self.valstep + 0.5)*self.valstep
        return float(np.clip(round(result, 12), self.valmin, self.valmax))


class PlaneSlicingPanel:
    def __init__(self, viewer, model):
        self.viewer, self.model = viewer, model
        viewer.ax.set_autoscale_on(False)
        self.slicer = PlaneSlicer(model)
        self.orientation = "Horizontal"
        self.section_step = 0.1
        self._changing = False
        self._suppress_activation = True
        self.show_slicing_3d = False
        self.section_artists = []
        self.plane_artist = None
        self.current_sections = {}
        self.current_section = Polygon()
        source_z = model.transition.source_z
        target_z = model.transition.target_z
        self.positions = {"Horizontal": (source_z+target_z)/2, "Vertical": 0., "Inclined": 0.}
        self.colors = {c.feature_id: c.dual_vertex.color for c in model.cells}
        fig = viewer.fig
        fig.set_size_inches(21, 10)
        viewer.ax.set_position([0.155, 0.35, 0.29, 0.52])
        viewer.overlay_panel.ax.set_position([0.475, 0.35, 0.22, 0.52])
        viewer.overlay_panel.status.set_position((0.46, 0.285))
        viewer.overlay_panel.status.set_fontsize(7)
        self.ax = fig.add_axes([0.75, 0.35, 0.23, 0.52])
        self.section_status = fig.text(0.75, 0.285, "", fontsize=8)
        fig.text(0.025, 0.267, "Plane orientation", fontsize=9)
        self.orientation_dropdown = OrientationDropdown(fig, [0.025, 0.225, 0.15, 0.035],
                                                         self.set_orientation)
        self.position_slider = _PositionSlider(
            fig.add_axes([0.29, 0.235, 0.35, 0.022]), "Z (m)",
            float(self.slicer.minimum[2]), float(self.slicer.maximum[2]),
            valinit=self.positions["Horizontal"], valstep=self.section_step,
            anchor=float(self.slicer.minimum[2]), valfmt="%1.6g")
        self.section_slider = self.position_slider
        self.lower_button = Button(fig.add_axes([0.70, 0.22, 0.065, 0.04]), "Lower")
        self.raise_button = Button(fig.add_axes([0.78, 0.22, 0.065, 0.04]), "Raise")
        self.step_box = TextBox(fig.add_axes([0.915, 0.225, 0.06, 0.035]), "Step (m): ", initial="0.1")
        self.visibility_checkbox = CheckButtons(
            fig.add_axes([0.015, 0.04, 0.14, 0.065]), ["Show slicing in 3D"], [False])
        self.visibility_checkbox.labels[0].set_fontsize(9)
        self.visibility_checkbox.drawon = False
        self.visibility_checkbox.on_clicked(self._visibility_changed)
        self.step_error = fig.text(0.86, 0.19, "", color="firebrick", fontsize=8)
        self.azimuth_slider = Slider(fig.add_axes([0.29, 0.155, 0.27, 0.022]),
                                     "Normal azimuth (°)", 0, 360, valinit=0, valstep=1, valfmt="%1.0f")
        self.tilt_slider = Slider(fig.add_axes([0.70, 0.155, 0.25, 0.022]),
                                  "Tilt (°)", 0, 90, valinit=45, valstep=1, valfmt="%1.0f")
        fig.text(0.18, 0.115, "Azimuth: 0° north (+Y), 90° east (+X).  Tilt: 0° horizontal, 90° vertical.",
                 fontsize=8, color="0.35")
        fig.text(0.18, 0.085,
                 "Move plane: drag position slider, scroll over a view or the slider, ↑/↓, or Lower/Raise.",
                 fontsize=8)
        viewer.navigation_hint.set_position((0.18, 0.045))
        viewer.navigation_hint.set_text("Drag 3D: rotate   f/s/a: navigate half-edges   c/x: shade/clear   q: close")
        self.position_slider.on_changed(self.set_position)
        for widget in (self.position_slider, self.azimuth_slider, self.tilt_slider):
            widget.drawon = False
        self.azimuth_slider.on_changed(self._angle_changed)
        self.tilt_slider.on_changed(self._angle_changed)
        self.step_box.on_submit(self.set_step)
        self.lower_button.on_clicked(lambda event: self.move_section(-1))
        self.raise_button.on_clicked(lambda event: self.move_section(1))
        self._set_angle_availability()
        for text in fig.texts:
            if text.get_text() == "Current half-edge":
                text.set_position((0.02, 0.335))
            elif text.get_text().startswith("Other face edges"):
                text.set_position((0.02, 0.305))
        self._install_scroll_handler()
        self.set_position(self.position_slider.val, user_initiated=False)
        self._suppress_activation = False

    @property
    def is_editing(self):
        return self.step_box.capturekeystrokes

    def _set_angle_availability(self):
        for widget, enabled in (
            (self.azimuth_slider, self.orientation != "Horizontal"),
            (self.tilt_slider, self.orientation == "Inclined"),
        ):
            widget.set_active(enabled)
            for artist in widget.ax.get_children():
                artist.set_alpha(1.0 if enabled else 0.25)

    def _plane(self, position):
        if self.orientation == "Horizontal":
            return SlicePlane.horizontal(position)
        tilt = 90. if self.orientation == "Vertical" else float(self.tilt_slider.val)
        return SlicePlane.from_angles(self.slicer.center, tilt, float(self.azimuth_slider.val), position)

    def _reset_bounds(self):
        if self.orientation == "Horizontal":
            lower, upper = map(float, (self.slicer.minimum[2], self.slicer.maximum[2]))
            anchor = lower
            label = "Z (m)"
        else:
            lower, upper = self.slicer.offset_bounds(self._plane(0))
            anchor = 0.
            label = "Offset (m)"
        self.position_slider.valmin, self.position_slider.valmax = lower, upper
        self.position_slider.anchor = anchor
        self.position_slider.ax.set_xlim(lower, upper)
        self.position_slider.label.set_text(label)

    def set_orientation(self, value):
        if value not in OrientationDropdown.OPTIONS:
            raise ValueError("Unknown plane orientation.")
        self.positions[self.orientation] = float(self.position_slider.val)
        self.orientation = value
        self.orientation_dropdown.select(value, notify=False)
        self._set_angle_availability()
        self._reset_bounds()
        self.set_position(self.positions[value])

    def _angle_changed(self, value):
        if self._changing:
            return
        self._reset_bounds()
        self.set_position(self.position_slider.val)

    def set_position(self, value, *, user_initiated=True):
        if self._changing:
            return
        value = self.position_slider._stepped_value(float(value))
        value = float(np.clip(value, self.position_slider.valmin, self.position_slider.valmax))
        self._changing = True
        try:
            self.position_slider.set_val(value)
        finally:
            self._changing = False
        self.positions[self.orientation] = value
        if user_initiated and not self._suppress_activation:
            self.set_3d_visible(True, redraw=False)
        self.current_plane = self._plane(value)
        self.redraw()

    def set_step(self, value):
        if self._changing:
            return
        try:
            step = float(value)
            if not isfinite(step) or step <= 0:
                raise ValueError
        except (ValueError, TypeError):
            self.step_error.set_text("Enter a positive finite step.")
            self._changing = True
            try:
                self.step_box.set_val(f"{self.section_step:g}")
            finally:
                self._changing = False
            self.viewer.fig.canvas.draw_idle()
            return
        self.step_error.set_text("")
        self.section_step = step
        self.position_slider.valstep = step
        self.set_position(self.position_slider.val, user_initiated=False)

    def move_section(self, steps):
        if steps == 0:
            return
        count = copysign(max(1, round(abs(steps))), steps)
        self.set_position(float(self.position_slider.val) + count*self.section_step)

    def _install_scroll_handler(self):
        canvas = self.viewer.fig.canvas
        self.scroll_fallbacks = []
        for cid, ref in list(canvas.callbacks.callbacks.get("scroll_event", {}).items()):
            callback = ref()
            if getattr(callback, "__name__", "") == "scroll_handler":
                self.scroll_fallbacks.append(callback)
                canvas.mpl_disconnect(cid)
        self.scroll_connection = canvas.mpl_connect("scroll_event", self.on_scroll)

    def on_scroll(self, event):
        if event.inaxes in (self.viewer.ax, self.viewer.overlay_panel.ax, self.ax,
                            self.position_slider.ax):
            steps = getattr(event, "step", 0)
            if steps == 0:
                steps = 1 if getattr(event, "button", None) == "up" else -1
            self.move_section(steps)
        else:
            for callback in self.scroll_fallbacks:
                callback(event)

    def _visibility_changed(self, label):
        self.show_slicing_3d = bool(self.visibility_checkbox.get_status()[0])
        self.redraw()

    def set_3d_visible(self, visible, *, redraw=True):
        self.show_slicing_3d = bool(visible)
        if self.visibility_checkbox.get_status()[0] != self.show_slicing_3d:
            self.visibility_checkbox.eventson = False
            try:
                self.visibility_checkbox.set_active(0)
            finally:
                self.visibility_checkbox.eventson = True
        if redraw:
            self.redraw()

    def snapshot_state(self):
        return dict(orientation=self.orientation, positions=self.positions.copy(),
                    step=self.section_step, azimuth=float(self.azimuth_slider.val),
                    tilt=float(self.tilt_slider.val), visible=self.show_slicing_3d)

    def restore_state(self, state):
        self._suppress_activation = True
        try:
            self.azimuth_slider.set_val(state["azimuth"])
            self.tilt_slider.set_val(state["tilt"])
            self.step_box.set_val(str(state["step"]))
            self.set_orientation(state["orientation"])
            self.positions.update(state["positions"])
            self.set_position(self.positions[self.orientation], user_initiated=False)
            self.set_3d_visible(state["visible"])
        finally:
            self._suppress_activation = False

    @staticmethod
    def _parts(geometry):
        if geometry.geom_type.startswith("Multi") or geometry.geom_type == "GeometryCollection":
            for part in geometry.geoms:
                yield from PlaneSlicingPanel._parts(part)
        elif not geometry.is_empty:
            yield geometry

    @staticmethod
    def _path_patch(polygon, color):
        paths = []
        for ring in (polygon.exterior, *polygon.interiors):
            xy = np.asarray(ring.coords)
            codes = [Path.MOVETO] + [Path.LINETO]*(len(xy)-2) + [Path.CLOSEPOLY]
            paths.append(Path(xy, codes))
        return PathPatch(Path.make_compound_path(*paths), facecolor=color,
                         edgecolor=color, alpha=0.55, lw=1.1)

    def redraw(self):
        plane = self.current_plane
        sections = self.slicer.sections(plane, include_degenerate=True)
        for artist in self.section_artists:
            artist.remove()
        self.section_artists.clear()
        if self.plane_artist is not None:
            self.plane_artist.remove()
            self.plane_artist = None
        if self.show_slicing_3d:
            outline = self.slicer.plane_outline(plane)
            if len(outline) >= 3:
                self.plane_artist = Poly3DCollection(
                    [outline], facecolor="0.65", edgecolor="0.35", alpha=0.14, linewidth=0.8)
                self.viewer.ax.add_collection3d(self.plane_artist)
        self.ax.clear()
        self.ax.set_aspect("equal", adjustable="box")
        self.ax.set_title(f"{self.orientation} cross-section", fontsize=10)
        horizontal = self.orientation == "Horizontal"
        self.ax.set_xlabel("Local X (m)" if horizontal else "Plane u (m)")
        self.ax.set_ylabel("Local Y (m)" if horizontal else "Plane v (m)")
        self.ax.grid(alpha=0.15)
        source_id = self.model.overlay.source_id
        for owner, geometry in sections.items():
            for part in self._parts(geometry):
                color = self.colors[owner]
                if part.geom_type == "Polygon":
                    part = orient(part, sign=1)
                    self.ax.add_patch(self._path_patch(part, color))
                    rings = (part.exterior, *part.interiors)
                    for ring in rings:
                        xy = np.asarray(ring.coords)
                        if self.show_slicing_3d:
                            xyz = plane.lift(xy)
                            self.section_artists.extend(self.viewer.ax.plot(
                                xyz[:, 0], xyz[:, 1], xyz[:, 2],
                                color="black" if owner == source_id else color, lw=1.6))
                        if owner == source_id:
                            self.ax.plot(xy[:, 0], xy[:, 1], color="black", lw=1.2)
                else:
                    xy = np.asarray(part.coords)
                    marker = "o" if part.geom_type == "Point" else None
                    self.ax.plot(xy[:, 0], xy[:, 1], color=color, lw=2, marker=marker, markersize=6)
                    if self.show_slicing_3d:
                        xyz = plane.lift(xy)
                        self.section_artists.extend(self.viewer.ax.plot(
                            xyz[:, 0], xyz[:, 1], xyz[:, 2],
                            color=color, lw=2, marker=marker, markersize=6))
        projected = plane.project(self.slicer.vertices)
        lower, upper = projected.min(axis=0), projected.max(axis=0)
        padding = np.maximum((upper-lower)*0.06, 0.1)
        self.ax.set_xlim(lower[0]-padding[0], upper[0]+padding[0])
        self.ax.set_ylim(lower[1]-padding[1], upper[1]+padding[1])
        if not sections:
            self.ax.text(0.5, 0.5, "No intersection with this plane", transform=self.ax.transAxes,
                         ha="center", va="center", fontsize=10)
        self.current_sections = sections
        self.current_section = sections.get(source_id, Polygon())
        unit = "Z" if horizontal else "Offset"
        description = f"{unit} = {self.position_slider.val:.6g} m | step = {self.section_step:g} m"
        areas = sum(p.area > 0 for p in sections.values())
        contacts = len(sections)-areas
        self.section_status.set_text(
            description + f"\n{areas} area sections | {contacts} point/line contacts"
            + f" | total area = {sum(p.area for p in sections.values()):.2f} m²")
        self.viewer.fig.canvas.draw_idle()
