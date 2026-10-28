"""Real mouse-event regressions for dropdown capture and target rebuilds."""

import unittest
from unittest.mock import patch

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.backend_bases import KeyEvent, MouseEvent
from matplotlib.widgets import TextBox

from plane_controls import OrientationDropdown
from test_collapse_targets import make_model
from viewer_session import ViewerSession


def raise_callback_error(error):
    raise error


def send_mouse(canvas, name, xy):
    canvas.callbacks.process(name, MouseEvent(name, canvas, *xy, button=1))


def click_xy(canvas, xy):
    send_mouse(canvas, "button_press_event", xy)
    send_mouse(canvas, "button_release_event", xy)


def click_axes(ax):
    click_xy(ax.figure.canvas, ax.transAxes.transform((0.5, 0.5)))


def choose(dropdown, value):
    click_axes(dropdown.button.ax)
    button = dropdown.menu[dropdown.options.index(value)]
    click_axes(button.ax)


class DropdownMouseTests(unittest.TestCase):
    def setUp(self):
        self.fig = plt.figure(figsize=(21, 10))
        self.box = TextBox(self.fig.add_axes([0.06, 0.67, 0.09, 0.045]), "Edge ID:")
        self.body = self.fig.add_axes([0.3, 0.3, 0.5, 0.5])
        self.chosen = []
        self.dropdown = OrientationDropdown(
            self.fig, [0.02, 0.61, 0.13, 0.032], self.chosen.append,
            options=("Polygon", "Point", "Polyline"), value="Polyline")
        self.fig.canvas.callbacks.exception_handler = raise_callback_error
        self.fig.canvas.draw()

    def tearDown(self):
        plt.close(self.fig)

    def assert_closed(self):
        self.assertFalse(self.dropdown.is_open)
        for button in self.dropdown.menu:
            self.assertFalse(button.ax.get_visible())
            self.assertFalse(button.active)
            self.assertFalse(button.eventson)

    def test_point_selection_does_not_compete_with_edge_id_textbox(self):
        self.assert_closed()
        with patch.object(self.fig.canvas, "draw_idle"):
            for value in ("Point", "Polygon", "Polyline", "Point"):
                choose(self.dropdown, value)
                self.assertEqual(self.dropdown.value, value)
                self.assertIsNone(self.fig.canvas.mouse_grabber)
                self.assertFalse(self.box.capturekeystrokes)
                self.assert_closed()
            click_axes(self.box.ax)
            self.assertTrue(self.box.capturekeystrokes)
            self.assertIsNone(self.fig.canvas.mouse_grabber)
        self.assertEqual(self.chosen, ["Point", "Polygon", "Polyline", "Point"])

    def test_outside_click_toggle_and_drag_release_cancel_cleanly(self):
        canvas = self.fig.canvas
        with patch.object(canvas, "draw_idle"):
            click_axes(self.dropdown.button.ax)
            click_axes(self.dropdown.button.ax)
            self.assert_closed()
            click_axes(self.dropdown.button.ax)
            click_axes(self.body)
            self.assert_closed()
            click_axes(self.dropdown.button.ax)
            option = self.dropdown.menu[1].ax
            send_mouse(canvas, "button_press_event", option.transAxes.transform((0.5, 0.5)))
            self.assertIs(canvas.mouse_grabber, option)
            send_mouse(canvas, "button_release_event", self.body.transAxes.transform((0.5, 0.5)))
            self.assert_closed()
            self.assertIsNone(canvas.mouse_grabber)
            self.assertFalse(self.chosen)

    def test_close_releases_only_capture_owned_by_dropdown(self):
        canvas = self.fig.canvas
        with patch.object(canvas, "draw_idle"):
            click_axes(self.dropdown.button.ax)
            option = self.dropdown.menu[1].ax
            xy = option.transAxes.transform((0.5, 0.5))
            send_mouse(canvas, "button_press_event", xy)
            self.dropdown.close()
            self.assertIsNone(canvas.mouse_grabber)
            self.assert_closed()
            send_mouse(canvas, "button_release_event", xy)
            xy = self.box.ax.transAxes.transform((0.5, 0.5))
            send_mouse(canvas, "button_press_event", xy)
            self.assertIs(canvas.mouse_grabber, self.box.ax)
            self.dropdown.close()
            self.assertIs(canvas.mouse_grabber, self.box.ax)
            send_mouse(canvas, "button_release_event", xy)
            self.assertIsNone(canvas.mouse_grabber)


class SessionMouseTests(unittest.TestCase):
    def setUp(self):
        self.session = ViewerSession(make_model("polyline"), make_model)
        self.fig = self.session.viewer.fig
        self.fig.canvas.callbacks.exception_handler = raise_callback_error
        self.fig.canvas.draw()

    def tearDown(self):
        plt.close(self.fig)

    def test_target_and_orientation_choices_use_real_press_release_events(self):
        session = self.session
        with patch.object(self.fig.canvas, "draw_idle"):
            session.viewer.plane_controls.set_position(6.2, user_initiated=False)
            for target in ("Point", "Polygon", "Polyline", "Point"):
                old_menu = session.target_dropdown
                choose(old_menu, target)
                self.assertEqual(session.model.transition.target_kind, target.lower())
                self.assertIsNone(self.fig.canvas.mouse_grabber)
                self.assertFalse(old_menu.is_open)
                self.assertFalse(any(b.active or b.eventson for b in old_menu.menu))
                controls = session.viewer.plane_controls
                self.assertAlmostEqual(controls.position_slider.val, 6.2)
                self.assertFalse(controls.show_slicing_3d)
            for orientation in ("Vertical", "Inclined", "Horizontal"):
                controls = session.viewer.plane_controls
                choose(controls.orientation_dropdown, orientation)
                self.assertEqual(controls.orientation, orientation)
                self.assertIsNone(self.fig.canvas.mouse_grabber)

    def test_text_slider_checkbox_and_keys_work_after_mouse_target_switch(self):
        canvas = self.fig.canvas
        with patch.object(canvas, "draw_idle"):
            choose(self.session.target_dropdown, "Point")
            viewer = self.session.viewer
            controls = viewer.plane_controls
            click_axes(viewer.text_box.ax)
            self.assertTrue(viewer.text_box.capturekeystrokes)
            # An outside press leaves text editing and changes the slice.
            click_axes(controls.position_slider.ax)
            self.assertFalse(viewer.text_box.capturekeystrokes)
            self.assertIsNone(canvas.mouse_grabber)
            self.assertTrue(controls.show_slicing_3d)
            event = KeyEvent("key_press_event", canvas, key="up")
            start = controls.position_slider.val
            canvas.callbacks.process("key_press_event", event)
            self.assertAlmostEqual(controls.position_slider.val, start + controls.section_step)
            label = controls.visibility_checkbox.labels[0]
            bounds = label.get_window_extent(canvas.get_renderer())
            click_xy(canvas, ((bounds.x0 + bounds.x1)/2, (bounds.y0 + bounds.y1)/2))
            self.assertFalse(controls.show_slicing_3d)
            self.assertIsNone(canvas.mouse_grabber)

    def test_failed_mouse_selection_leaves_original_controls_usable(self):
        viewer = self.session.viewer
        def fail(kind):
            raise ValueError("unsupported mouse test target")
        with patch.object(self.fig.canvas, "draw_idle"):
            self.session.builder = fail
            choose(self.session.target_dropdown, "Point")
            self.assertIs(self.session.viewer, viewer)
            self.assertEqual(self.session.target_dropdown.value, "Polyline")
            self.assertIsNone(self.fig.canvas.mouse_grabber)
            self.assertFalse(self.session.target_dropdown.is_open)
            self.assertIn("unsupported mouse test target", self.session.error_text.get_text())
            choose(viewer.plane_controls.orientation_dropdown, "Vertical")
            self.assertEqual(viewer.plane_controls.orientation, "Vertical")

    def test_rebuild_cancels_drag_before_removing_its_axes(self):
        canvas = self.fig.canvas
        old_slider = self.session.viewer.plane_controls.position_slider.ax
        with patch.object(canvas, "draw_idle"):
            send_mouse(canvas, "button_press_event", old_slider.transAxes.transform((0.5, 0.5)))
            self.assertIs(canvas.mouse_grabber, old_slider)
            self.assertTrue(self.session.switch_target("point"), self.session.last_error)
            self.assertNotIn(old_slider, self.fig.axes)
            self.assertIsNone(canvas.mouse_grabber)
            choose(self.session.target_dropdown, "Polygon")
            self.assertEqual(self.session.model.transition.target_kind, "polygon")
            self.assertIsNone(canvas.mouse_grabber)


if __name__ == "__main__":
    unittest.main()
