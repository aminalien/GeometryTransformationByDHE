"""Reserve this application's address before launching its browser."""
import errno
import socket
import threading
import webbrowser


def bind_listener(port=None,*,preferred_port=8766):
    if port is not None and not 0<=port<=65535:raise ValueError('Port must be between 0 and 65535.')
    listener=socket.socket(socket.AF_INET,socket.SOCK_STREAM)
    try:
        if hasattr(socket,'SO_EXCLUSIVEADDRUSE'):
            listener.setsockopt(socket.SOL_SOCKET,socket.SO_EXCLUSIVEADDRUSE,1)
        try:listener.bind(('127.0.0.1',preferred_port if port is None else port))
        except OSError as exc:
            if port is not None or exc.errno not in (errno.EADDRINUSE,10048):raise
            listener.bind(('127.0.0.1',0))
        listener.listen(128)
        return listener
    except BaseException:
        listener.close();raise


def open_when_ready(server,url,stopped,opener=None):
    while not stopped.is_set():
        if server.started:
            (opener or webbrowser.open)(url);return
        if server.should_exit:return
        stopped.wait(.05)


def run_local(app,*,port=None,open_browser=True):
    import uvicorn
    with bind_listener(port) as listener:
        actual_port=listener.getsockname()[1]
        url=f'http://127.0.0.1:{actual_port}'
        print('DHE Transition Atlas: '+url,flush=True)
        server=uvicorn.Server(uvicorn.Config(app,host='127.0.0.1',port=actual_port,workers=1))
        stopped=threading.Event()
        if open_browser:
            threading.Thread(target=open_when_ready,args=(server,url,stopped),daemon=True).start()
        try:server.run(sockets=[listener])
        finally:stopped.set()
