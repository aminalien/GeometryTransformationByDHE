"""Transactional transformation/stage navigation on the paper branch."""

from copy import copy

import matplotlib.pyplot as plt
from matplotlib.widgets import Button

import DHECore as dhe
from paper_stages import STAGES, TRANSFORMATIONS, build_paper_stage
from paper_plot import mount_paper_view
from plane_controls import OrientationDropdown
from viewer_session import _batch_draw, _core_state, _restore_core


def camera_state(ax):
    return dict(elev=float(ax.elev), azim=float(ax.azim), roll=float(ax.roll),
                xlim=list(map(float, ax.get_xlim())), ylim=list(map(float, ax.get_ylim())),
                zlim=list(map(float, ax.get_zlim())), box_aspect=list(map(float, ax.get_box_aspect())))


def restore_camera(ax, state):
    ax.view_init(elev=state["elev"], azim=state["azim"], roll=state["roll"])
    for name in ("xlim", "ylim", "zlim"):
        getattr(ax, "set_"+name)(state[name])
    ax.set_box_aspect(state["box_aspect"])


class PaperSession:
    def __init__(self, args, transformation="aggregation", step="final", *, stage=None, builder=None):
        self.args = copy(args)
        self.builder = builder or (lambda family, selected: build_paper_stage(self.args, family, selected))
        self.stage = stage or self.builder(transformation, step)
        self.last_steps = {key: "final" for key in STAGES}
        self.last_steps[self.stage.transformation] = self.stage.spec.id
        self.cameras, self.planes = {}, {}
        self.connections = set()
        self.busy = False
        self.last_error = ""
        self.figure = plt.figure(figsize=(21, 10))
        self.native_scroll = [ref() for ref in self.figure.canvas.callbacks.callbacks.get("scroll_event", {}).values()
                              if ref() is not None and getattr(ref(), "__name__", "") == "scroll_handler"]
        with _batch_draw(self.figure.canvas):
            self._mount(self.stage)
        self.figure.canvas.draw_idle()

    def _callbacks(self):
        return {(event, cid) for event, callbacks in self.figure.canvas.callbacks.callbacks.items() for cid in callbacks}

    def _restore_scroll(self):
        callbacks = [ref() for ref in self.figure.canvas.callbacks.callbacks.get("scroll_event", {}).values()]
        for callback in self.native_scroll:
            if callback not in callbacks:
                self.figure.canvas.mpl_connect("scroll_event", callback)

    def _disconnect(self):
        canvas = self.figure.canvas
        if canvas.mouse_grabber is not None:
            canvas.release_mouse(canvas.mouse_grabber)
        for _, cid in self.connections:
            canvas.mpl_disconnect(cid)
        self.connections.clear()
        self._restore_scroll()

    def _mount(self, stage):
        baseline = self._callbacks()
        try:
            self.viewer = mount_paper_view(stage, self.figure)
            self.viewer.paper_session = self
            self.figure.text(0.015, 0.947, "Transformation", fontsize=9)
            self.transformation_dropdown = OrientationDropdown(
                self.figure, [0.12, 0.925, 0.24, 0.032], self.switch_transformation,
                options=tuple(TRANSFORMATIONS.values()), value=TRANSFORMATIONS[stage.transformation])
            self.figure.text(0.39, 0.947, "Implementation step", fontsize=9)
            titles = [f"{i}. {s.title}" for i, s in enumerate(STAGES[stage.transformation], 1)]
            selected = STAGES[stage.transformation].index(stage.spec)
            self.step_dropdown = OrientationDropdown(
                self.figure, [0.50, 0.925, 0.36, 0.032], self._on_step,
                options=titles, value=titles[selected])
            # Only one popup may be open; inactive hidden axes cannot grab input.
            for dropdown in (self.transformation_dropdown, self.step_dropdown):
                original = dropdown.callback
                dropdown.callback = lambda value, callback=original: (self._close_menus(), callback(value))[-1]
            self.menu_connection = self.figure.canvas.mpl_connect("button_press_event", self._close_other_menu)
            self.generalization_button = Button(self.figure.add_axes([0.015, 0.005, 0.15, 0.03]), "Generalization…")
            self.generalization_button.on_clicked(lambda event: self.open_generalization())
            self.export_button = Button(self.figure.add_axes([0.18, 0.005, 0.12, 0.03]), "Export figure")
            self.export_section_button = Button(self.figure.add_axes([0.31, 0.005, 0.12, 0.03]), "Export section")
            self.export_section_button.set_active(stage.final)
            if not stage.final:
                self.export_section_button.label.set_color("0.6")
            self.export_button.on_clicked(lambda event: self.export_figure())
            self.export_section_button.on_clicked(lambda event: self.export_figure(section=True))
            self.status = self.figure.text(0.45, 0.014, "PNG + SVG + settings JSON → artifacts/paper_figures", fontsize=8)
            self.error_text = self.figure.text(0.50, 0.905, "", ha="center", fontsize=8, color="firebrick")
            if stage.transformation in self.cameras:
                restore_camera(self.viewer.ax, self.cameras[stage.transformation])
            if stage.final and stage.transformation in self.planes:
                self.viewer.plane_controls.restore_state(self.planes[stage.transformation])
            if hasattr(self.viewer, "_store_view_state"):
                self.viewer._store_view_state()
            if stage.model:
                stage.model.paper_session = self
        finally:
            self.connections = self._callbacks() - baseline

    def _close_other_menu(self, event):
        dropdowns = [self.transformation_dropdown, self.step_dropdown]
        controls = getattr(self.viewer, "plane_controls", None)
        if controls:
            dropdowns.append(controls.orientation_dropdown)
        for dropdown in dropdowns:
            if event.inaxes is dropdown.button.ax:
                for other in dropdowns:
                    if other is not dropdown:
                        other.close(draw=False)
                break

    def _close_menus(self):
        self.transformation_dropdown.close(draw=False)
        self.step_dropdown.close(draw=False)
        controls = getattr(self.viewer, "plane_controls", None)
        if controls:
            controls.orientation_dropdown.close(draw=False)

    def _on_step(self, title):
        index = self.step_dropdown.options.index(title)
        return self.switch_stage(STAGES[self.stage.transformation][index].id)

    def _remember(self):
        self.cameras[self.stage.transformation] = camera_state(self.viewer.ax)
        controls = getattr(self.viewer, "plane_controls", None)
        if controls:
            self.planes[self.stage.transformation] = controls.snapshot_state()

    def switch_transformation(self, value):
        family = next((key for key, label in TRANSFORMATIONS.items() if value in (key, label)), None)
        if family is None:
            raise ValueError("Unknown transformation.")
        return self._switch(family, self.last_steps[family])

    def switch_stage(self, step):
        return self._switch(self.stage.transformation, step)

    def _reset_dropdown_values(self):
        self.transformation_dropdown.select(TRANSFORMATIONS[self.stage.transformation], notify=False)
        i = STAGES[self.stage.transformation].index(self.stage.spec)
        self.step_dropdown.select(self.step_dropdown.options[i], notify=False)

    def _switch(self, family, step):
        if self.busy:
            return False
        if family == self.stage.transformation and step == self.stage.spec.id:
            self._reset_dropdown_values()
            return True
        old_stage, old_core = self.stage, _core_state()
        self._remember()
        # New families retain orientation, but fit their own source extent.
        angles = {key: getattr(self.viewer.ax, key) for key in ("elev", "azim", "roll")}
        self._close_menus()
        canvas = self.figure.canvas
        if canvas.mouse_grabber is not None:
            canvas.release_mouse(canvas.mouse_grabber)
        self.busy = True
        self.error_text.set_text("Building selected stage…")
        canvas.draw_idle()
        try:
            replacement = self.builder(family, step)
        except Exception as exc:
            _restore_core(old_core)
            self.last_error = str(exc)
            self._reset_dropdown_values()
            self.error_text.set_text("Stage unchanged: " + self.last_error)
            self.busy = False
            canvas.draw_idle()
            return False
        self._disconnect()
        try:
            with _batch_draw(canvas):
                self._mount(replacement)
                if family not in self.cameras:
                    self.viewer.ax.view_init(**angles)
                    if hasattr(self.viewer, "_store_view_state"):
                        self.viewer._store_view_state()
            self.stage = replacement
            self.last_steps[family] = step
            self.last_error = ""
        except Exception as exc:
            self._disconnect()
            _restore_core(old_core)
            self.stage = old_stage
            with _batch_draw(canvas):
                self._mount(old_stage)
            self.last_error = str(exc)
            self.error_text.set_text("Stage unchanged: " + self.last_error)
            return False
        finally:
            self.busy = False
        canvas.draw_idle()
        return True

    def export_figure(self, *, section=False, directory=None):
        from paper_export import export_paper_figure
        try:
            paths = export_paper_figure(self, directory=directory or getattr(self.args, "figure_dir", "artifacts/paper_figures"), section=section)
            self.status.set_text("Saved " + str(paths[0]))
            self.figure.canvas.draw_idle()
            return paths
        except Exception as exc:
            self.status.set_text("Export failed: " + str(exc))
            self.figure.canvas.draw_idle()
            return None

    def open_generalization(self):
        from generalization_session import launch_generalization
        return launch_generalization()

    def show(self):
        plt.show()
