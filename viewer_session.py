"""Transactional target switching while reusing a single Matplotlib window."""

import DHECore as dhe
from contextlib import contextmanager
import matplotlib.pyplot as plt
from plane_controls import OrientationDropdown
from plot_dhe_interactive import interactive_plot_from_lists


def _core_state():
    return dhe.dhe, dhe.killdhe, dhe.Vertex.cntId, dhe.DualHalfEdge.cntId


def _restore_core(state):
    dhe.dhe, dhe.killdhe, dhe.Vertex.cntId, dhe.DualHalfEdge.cntId = state


@contextmanager
def _batch_draw(canvas):
    draw_idle = canvas.draw_idle
    canvas.draw_idle = lambda *args, **kwargs: None
    try:
        yield
    finally:
        canvas.draw_idle = draw_idle


class ViewerSession:
    def __init__(self, model, builder):
        self.builder = builder
        self.model = model
        self.viewer = None
        self.connections = set()
        self.busy = False
        self.last_error = ""
        figure = plt.figure(figsize=(11, 7))
        with _batch_draw(figure.canvas):
            self._mount(model, figure)
        figure.canvas.draw_idle()

    def _mount(self, model, figure=None):
        viewer = interactive_plot_from_lists(
            model.active_edges(), dhe.Getdhe()[1], show=False, highlight_cells=True,
            overlay=model.overlay, transition=model.transition, figure=figure)
        viewer.session = self
        viewer.fig.text(0.02, 0.655, "Target geometry", fontsize=9)
        self.target_dropdown = OrientationDropdown(
            viewer.fig, [0.02, 0.61, 0.13, 0.032], self._on_target,
            options=("Polygon", "Point", "Polyline"),
            value=model.transition.target_kind.title())
        self.error_text = viewer.fig.text(0.20, 0.935, "", fontsize=9, color="firebrick")
        self.viewer = viewer
        self.model = model
        model.viewer_session = self
        self.connections = {
            (event, cid) for event, callbacks in viewer.fig.canvas.callbacks.callbacks.items()
            for cid in callbacks} - viewer.baseline_connections

    def _on_target(self, label):
        self.switch_target(label.lower())

    def switch_target(self, kind):
        kind = kind.lower()
        current = self.model.transition.target_kind
        if self.busy or kind == current:
            self.target_dropdown.select(current.title(), notify=False)
            return kind == current
        if kind not in ("polygon", "point", "polyline"):
            raise ValueError("Unknown target kind.")
        self.busy = True
        old_core = _core_state()
        old_viewer = self.viewer
        canvas = old_viewer.fig.canvas
        self.target_dropdown.close(draw=False)
        old_viewer.plane_controls.orientation_dropdown.close(draw=False)
        # Switching targets cancels a drag before any of its axes are retired.
        if canvas.mouse_grabber is not None:
            canvas.release_mouse(canvas.mouse_grabber)
        state = old_viewer.plane_controls.snapshot_state()
        old_viewer._store_view_state()
        camera = old_viewer._view_state.copy()
        self.error_text.set_text("Rebuilding target…")
        old_viewer.fig.canvas.draw_idle()
        try:
            replacement = self.builder(kind)
        except Exception as exc:
            _restore_core(old_core)
            self.last_error = str(exc)
            self.error_text.set_text("Target unchanged: " + self.last_error)
            self.target_dropdown.select(current.title(), notify=False)
            self.busy = False
            old_viewer.fig.canvas.draw_idle()
            return False
        canvas = old_viewer.fig.canvas
        for _, cid in self.connections:
            canvas.mpl_disconnect(cid)
        for callback in old_viewer.plane_controls.scroll_fallbacks:
            canvas.mpl_connect("scroll_event", callback)
        try:
            with _batch_draw(canvas):
                self._mount(replacement, old_viewer.fig)
                self.viewer.plane_controls.restore_state(state)
                self.viewer._view_state = camera
                self.viewer._restore_view_state()
            self.last_error = ""
        except Exception:
            # Widget/backend failures also restore a usable old model/window.
            _restore_core(old_core)
            if canvas.mouse_grabber is not None:
                canvas.release_mouse(canvas.mouse_grabber)
            baseline = old_viewer.baseline_connections
            for event, callbacks in list(canvas.callbacks.callbacks.items()):
                for cid in list(callbacks):
                    if (event, cid) not in baseline:
                        canvas.mpl_disconnect(cid)
            self._mount(self.model if self.model is not replacement else old_viewer.plane_controls.model,
                        old_viewer.fig)
            self.viewer.plane_controls.restore_state(state)
            self.viewer._view_state = camera
            self.viewer._restore_view_state()
            self.last_error = "Could not replace the viewer."
            self.error_text.set_text(self.last_error)
            self.busy = False
            return False
        self.busy = False
        self.viewer.fig.canvas.draw_idle()
        return True
