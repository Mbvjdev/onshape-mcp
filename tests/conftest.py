"""Shared fixtures for onshape-mcp tests.

All HTTP calls to Onshape are mocked — no real API requests are made.
"""

import os
import json
from unittest.mock import MagicMock, patch

import pytest

# Make sure auth doesn't fail at OnshapeClient import time
os.environ.setdefault("ONSHAPE_DEV_ACCESS", "test_access_key")
os.environ.setdefault("ONSHAPE_DEV_SECRET", "test_secret_key")

from onshape_mcp import rate_limiter as rl_mod
from onshape_mcp.client import OnshapeClient
from onshape_mcp.rate_limiter import RateLimiter


# ── Sample data ────────────────────────────────────────────────────

@pytest.fixture
def sample_documents():
    return {
        "items": [
            {
                "id": "did_111",
                "name": "TestDoc1",
                "owner": {"name": "alice"},
                "createdAt": "2025-01-01T00:00:00Z",
                "modifiedAt": "2025-01-02T00:00:00Z",
                "trash": False,
            },
            {
                "id": "did_222",
                "name": "TestDoc2",
                "owner": {"name": "bob"},
                "createdAt": "2025-01-03T00:00:00Z",
                "modifiedAt": "2025-01-04T00:00:00Z",
                "trash": False,
            },
        ]
    }


@pytest.fixture
def sample_document_info():
    return {
        "id": "did_111",
        "name": "TestDoc1",
        "owner": {"name": "alice"},
        "defaultWorkspace": {"id": "wid_aaa", "name": "Main"},
    }


@pytest.fixture
def sample_elements():
    return [
        {"id": "eid_ps1", "name": "Part Studio 1", "type": "PARTSTUDIO"},
        {"id": "eid_asm", "name": "Assembly 1", "type": "ASSEMBLY"},
    ]


@pytest.fixture
def sample_features():
    return {
        "features": [
            {
                "message": {
                    "featureId": "FID_sketch1",
                    "name": "Sketch 1",
                    "btType": "BTMSketch-151",
                    "suppressed": False,
                },
                "featureType": "newSketch",
            },
            {
                "message": {
                    "featureId": "FID_extrude1",
                    "name": "Extrude 1",
                    "btType": "BTMFeature-134",
                    "suppressed": False,
                },
                "featureType": "extrude",
            },
        ]
    }


@pytest.fixture
def sample_parts():
    return [
        {
            "partId": "PID_1",
            "name": "Body 1",
            "bodyType": "solid",
            "mass": {"value": 0.123},
            "volume": {"value": 0.0001},
            "material": {"displayName": "Steel"},
        },
        {
            "partId": "PID_2",
            "name": "Body 2",
            "bodyType": "solid",
            "mass": None,
            "volume": None,
            "material": None,
        },
    ]


# ── Rate limiter ────────────────────────────────────────────────────

@pytest.fixture
def reset_rate_limiter():
    """Reset the global rate limiter singleton between tests.

    Replaces it with a fast (zero-delay) limiter so tests aren't slowed
    down by the conservative production defaults.
    """
    with rl_mod._global_lock:
        rl_mod._global_limiter = RateLimiter(
            max_calls=10000,
            window=60.0,
            min_interval=0.0,
            backoff_base=0.0,
            backoff_max=0.0,
        )
    yield rl_mod._global_limiter
    with rl_mod._global_lock:
        rl_mod._global_limiter = None


# ── Mock HTTP backend ───────────────────────────────────────────────

class MockResponse:
    """Stand-in for httpx.Response."""

    def __init__(self, status_code=200, json_data=None, content=b"", headers=None):
        self.status_code = status_code
        self._json = json_data
        self.content = content
        self.headers = headers or {}

    def json(self):
        if self._json is None:
            raise ValueError("No JSON body")
        return self._json


class MockHttp:
    """Mock httpx.Client. Routes by (METHOD, url-path-suffix) to a queue.

    Use `set_route(method, url_substr, response)` to register one-shot or
    sticky responses. Stickier matchers register a list; we pop from
    front and reuse the last entry if the queue is empty.
    """

    def __init__(self):
        # list of (method, substr, [responses])
        self._routes = []
        self.calls = []  # log of (method, url, params, json)

    def set_route(self, method, substr, responses):
        if not isinstance(responses, list):
            responses = [responses]
        self._routes.append([method.upper(), substr, responses])

    def _dispatch(self, method, url, params=None, json_data=None):
        self.calls.append((method, url, params, json_data))
        for entry in self._routes:
            m, substr, resps = entry
            if m == method and substr in url:
                if len(resps) > 1:
                    return resps.pop(0)
                return resps[0]
        # Default: 200 empty
        return MockResponse(200, json_data={})

    def get(self, url, params=None, headers=None):
        return self._dispatch("GET", url, params=params)

    def post(self, url, params=None, json=None, headers=None):
        return self._dispatch("POST", url, params=params, json_data=json)

    def delete(self, url, params=None, headers=None):
        return self._dispatch("DELETE", url, params=params)

    def close(self):
        pass


@pytest.fixture
def mock_http():
    return MockHttp()


@pytest.fixture
def mock_client(mock_http, reset_rate_limiter):
    """OnshapeClient with HTTP layer replaced by MockHttp."""
    client = OnshapeClient()
    client._http = mock_http
    yield client
    client.close()
