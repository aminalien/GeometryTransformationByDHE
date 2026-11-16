"""The browser must reach the server owning the reserved project socket."""
import socket
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock,patch

from web_launch import bind_listener,open_when_ready,run_local


class LaunchTests(unittest.TestCase):
    def test_occupied_preferred_port_uses_different_reserved_address(self):
        with bind_listener(0) as occupied:
            port=occupied.getsockname()[1]
            with bind_listener(preferred_port=port) as listener:
                self.assertNotEqual(listener.getsockname()[1],port)
                self.assertEqual(listener.getsockname()[0],'127.0.0.1')
            with self.assertRaises(OSError):bind_listener(port)
        for port in (-1,65536):
            with self.assertRaises(ValueError):bind_listener(port)

    def test_ready_failure_and_stop_conditions(self):
        opener=Mock();stopped=threading.Event()
        open_when_ready(SimpleNamespace(started=True,should_exit=False),'project',stopped,opener)
        opener.assert_called_once_with('project');opener.reset_mock()
        open_when_ready(SimpleNamespace(started=False,should_exit=True),'other',stopped,opener)
        stopped.set();open_when_ready(SimpleNamespace(started=True,should_exit=False),'other',stopped,opener)
        opener.assert_not_called()

    def test_run_opens_actual_bound_port_only_after_start(self):
        opened=threading.Event();urls=[]
        def browser(url):urls.append(url);opened.set()
        class Server:
            started=False;should_exit=False
            def __init__(self,config):self.config=config
            def run(self,*,sockets):
                self.port=sockets[0].getsockname()[1]
                self.started=True
                self.assert_opened=opened.wait(2)
        with patch('uvicorn.Server',Server),patch('web_launch.webbrowser.open',side_effect=browser):
            run_local(object(),port=0)
        self.assertEqual(len(urls),1);self.assertRegex(urls[0],r'^http://127\.0\.0\.1:\d+$')
        with patch('uvicorn.Server') as server,patch('web_launch.webbrowser.open') as browser:
            run_local(object(),port=0,open_browser=False)
            browser.assert_not_called();server.return_value.run.assert_called_once()


if __name__=='__main__':unittest.main()
