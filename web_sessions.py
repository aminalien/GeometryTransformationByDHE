"""Bounded anonymous workspaces sharing one process-wide DHE construction worker."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from threading import RLock
import time
from uuid import uuid4

from fastapi import HTTPException,Request


def request_workspace(request:Request):
    return request.state.workspace


@dataclass
class Visitor:
    workspace:object
    touched:float
    requests:int=0


class WorkspaceRegistry:
    def __init__(self,factory,*,max_sessions=3,idle_seconds=1800,clock=time.monotonic):
        if max_sessions<1 or idle_seconds<=0:raise ValueError('Session limits must be positive.')
        self.factory=factory;self.max_sessions=max_sessions;self.idle_seconds=idle_seconds;self.clock=clock
        self.pool=ThreadPoolExecutor(max_workers=1,thread_name_prefix='DHE cloud worker')
        self.entries={};self.lock=RLock()

    def prune(self):
        with self.lock:
            now=self.clock()
            for token,visitor in list(self.entries.items()):
                if not visitor.requests and not visitor.workspace.running and now-visitor.touched>=self.idle_seconds:
                    del self.entries[token];visitor.workspace.close()

    def acquire(self,token):
        with self.lock:
            self.prune()
            if token not in self.entries:
                if len(self.entries)>=self.max_sessions:
                    raise HTTPException(503,'The demo is at visitor capacity. Please try again later.',headers={'Retry-After':'60'})
                token=uuid4().hex
                workspace=self.factory(self.pool)
                try:workspace.submit('initialize',0)
                except BaseException:workspace.close();raise
                self.entries[token]=Visitor(workspace,self.clock())
            visitor=self.entries[token];visitor.touched=self.clock();visitor.requests+=1
            return token,visitor.workspace

    def release(self,token):
        with self.lock:
            visitor=self.entries.get(token)
            if visitor is not None:visitor.requests-=1;visitor.touched=self.clock()

    def close(self):
        self.pool.shutdown(wait=True,cancel_futures=False)
        with self.lock:
            for visitor in self.entries.values():visitor.workspace.close()
            self.entries.clear()
