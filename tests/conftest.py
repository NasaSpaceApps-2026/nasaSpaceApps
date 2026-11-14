"""Session fixture: a devapi server on a free localhost port, exported as ATLAS_API_URL.

Tests that launch the real MCP server as a subprocess (tests/test_agent_*.py, via agent.mcp_bridge.stdio_params,
which copies os.environ) need a REST API to talk to, since the MCP server is a stateless wrapper over it.
Tests that build their own app (test_devapi.py, test_server.py) are unaffected.
"""
import os
import socket
import threading
import time

import pytest
import uvicorn

from atlas_core.testing import make_site
from devapi import create_app


@pytest.fixture(scope="session", autouse=True)
def shared_devapi():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(create_app(make_site(), synthetic=True), host="127.0.0.1", port=port,
                                           log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    for _ in range(100):
        if server.started:
            break
        time.sleep(0.05)
    old = os.environ.get("ATLAS_API_URL")
    os.environ["ATLAS_API_URL"] = f"http://127.0.0.1:{port}"
    yield os.environ["ATLAS_API_URL"]
    if old is None:
        os.environ.pop("ATLAS_API_URL", None)
    else:
        os.environ["ATLAS_API_URL"] = old
    server.should_exit = True
    thread.join(5)
