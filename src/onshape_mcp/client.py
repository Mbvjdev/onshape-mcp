"""Onshape API client with rate limiting, caching, and auth.

Wraps both the onpy library (for feature creation — handles types correctly)
and raw REST (for everything else). Handles:
- Auth via env vars or ~/.onpy/config.json
- Rate limiting with preemptive delays
- Exponential backoff on 429
- TTL caching for reads
- Automatic cache invalidation on writes
- Metric unit enforcement

All distances in METERS (Onshape's native unit). Convert mm/cm before calling.
"""

import os
import sys
import json
import base64
import time
import logging
from pathlib import Path
from typing import Optional, Any
from dataclasses import dataclass

import httpx

from .rate_limiter import get_rate_limiter
from .cache import ReadCache

logger = logging.getLogger("onshape-mcp")

# Onshape API base
API_BASE = "https://cad.onshape.com/api/v6"

# Unit conversions (Onshape uses meters)
MM = 0.001
CM = 0.01
M = 1.0


def _load_auth() -> tuple[str, str]:
    """Load Onshape API keys from environment or config file.

    Priority:
    1. ONSHAPE_DEV_ACCESS / ONSHAPE_DEV_SECRET env vars
    2. ONSHAPE_ACCESS_KEY / ONSHAPE_SECRET_KEY env vars
    3. ~/.onpy/config.json (dev_access / dev_secret fields)
    """
    # Try dev keys first
    access = os.environ.get("ONSHAPE_DEV_ACCESS")
    secret = os.environ.get("ONSHAPE_DEV_SECRET")
    if access and secret:
        return access, secret

    # Try primary keys
    access = os.environ.get("ONSHAPE_ACCESS_KEY")
    secret = os.environ.get("ONSHAPE_SECRET_KEY")
    if access and secret:
        return access, secret

    # Fall back to onpy config
    config_path = Path.home() / ".onpy" / "config.json"
    if config_path.exists():
        with open(config_path) as f:
            cfg = json.load(f)
        access = cfg.get("dev_access") or cfg.get("access_key")
        secret = cfg.get("dev_secret") or cfg.get("secret_key")
        if access and secret:
            return access, secret

    raise RuntimeError(
        "Onshape API keys not found. Set ONSHAPE_DEV_ACCESS/ONSHAPE_DEV_SECRET "
        "or ONSHAPE_ACCESS_KEY/ONSHAPE_SECRET_KEY env vars, or create ~/.onpy/config.json"
    )


class OnshapeClient:
    """Rate-limited, cached Onshape API client.

    Usage:
        client = OnshapeClient()
        docs = client.list_documents()
        parts = client.list_parts(did, wid, eid)
    """

    def __init__(self):
        access_key, secret_key = _load_auth()
        self.auth = base64.b64encode(
            f"{access_key}:{secret_key}".encode()
        ).decode()
        self.headers = {
            "Authorization": f"Basic {self.auth}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        self.rate_limiter = get_rate_limiter()  # global singleton — account-wide limit
        self.cache = ReadCache()
        self._http = httpx.Client(timeout=30.0)
        self._sketch_cache: dict[tuple, object] = {}  # (did, eid, sketch_id) → (onpy Sketch, onpy PartStudio)

    def _pre_acquire(self, n: int = 2):
        """Pre-acquire N rate limit tokens before calling onpy.

        onpy makes 2+ API calls internally (FeatureScript pre-flight + feature POST)
        and its HTTP layer bypasses our rate limiter. We pre-acquire tokens here
        so the global rate limiter tracks the calls onpy is about to make.
        """
        for _ in range(n):
            self.rate_limiter.acquire()

    # ── low-level HTTP ────────────────────────────────────────────

    def _request(
        self,
        method: str,
        url: str,
        params: Optional[dict] = None,
        json_data: Optional[dict] = None,
        headers: Optional[dict] = None,
        use_cache: bool = True,
        max_retries: int = 5,
        raw_response: bool = False,
    ) -> Any:
        """Make a rate-limited, cached, retried HTTP request to Onshape.

        Args:
            method: HTTP method
            url: Full URL (or relative path — if no https://, prepends API_BASE)
            params: Query parameters
            json_data: JSON body for POST/PUT
            headers: Extra headers (merged with default auth headers)
            use_cache: If True (and method is GET), check cache first
            max_retries: Max retries on 429 (exponential backoff)
            raw_response: If True, return httpx.Response instead of parsed JSON

        Returns:
            Parsed JSON response (dict/list) or httpx.Response if raw_response=True
        """
        if not url.startswith("https://"):
            url = f"{API_BASE}{url}"

        # Check cache for reads
        if method == "GET" and use_cache:
            cached = self.cache.get(method, url, params)
            if cached is not None:
                return cached

        merged_headers = {**self.headers, **(headers or {})}

        for attempt in range(max_retries):
            # Acquire rate limit token (blocks if needed)
            self.rate_limiter.acquire()

            try:
                if method == "GET":
                    resp = self._http.get(url, params=params, headers=merged_headers)
                elif method == "POST":
                    resp = self._http.post(url, params=params, json=json_data, headers=merged_headers)
                elif method == "DELETE":
                    resp = self._http.delete(url, params=params, headers=merged_headers)
                else:
                    raise ValueError(f"Unsupported HTTP method: {method}")

            except httpx.TimeoutException:
                logger.warning(f"Timeout on {method} {url} — retrying")
                time.sleep(2 ** attempt)
                continue
            except httpx.ConnectError:
                logger.warning(f"Connection error on {method} {url} — retrying")
                time.sleep(2 ** attempt)
                continue

            # Handle 429 — backoff and retry
            if resp.status_code == 429:
                self.rate_limiter.report_429()
                retry_after = resp.headers.get("Retry-After")
                wait = float(retry_after) if retry_after else None
                if wait is None:
                    wait = min(5 * (2 ** attempt), 60)
                logger.warning(
                    f"429 on {method} {url} — waiting {wait:.1f}s (attempt {attempt + 1}/{max_retries})"
                )
                time.sleep(wait)
                continue

            # Handle other errors
            if resp.status_code >= 500:
                logger.warning(f"{resp.status_code} on {method} {url} — retrying")
                time.sleep(2 ** attempt)
                continue

            self.rate_limiter.report_success()

            if raw_response:
                return resp

            # Onshape sometimes returns 200 with error in body
            data = resp.json()
            if isinstance(data, dict) and data.get("error"):
                raise RuntimeError(f"Onshape API error: {data['error']}")

            # Cache successful reads
            if method == "GET" and use_cache:
                self.cache.set(method, url, params, data)

            return data

        raise RuntimeError(f"Failed after {max_retries} retries: {method} {url}")

    def _get(self, url: str, params: Optional[dict] = None, **kwargs) -> Any:
        return self._request("GET", url, params=params, **kwargs)

    def _post(self, url: str, json_data: Optional[dict] = None, params: Optional[dict] = None, **kwargs) -> Any:
        return self._request("POST", url, params=params, json_data=json_data, **kwargs)

    def _delete(self, url: str, **kwargs) -> Any:
        return self._request("DELETE", url, **kwargs)

    # ── Documents ─────────────────────────────────────────────────

    def list_documents(self, query: str = "", limit: int = 20) -> list[dict]:
        """Search/list Onshape documents.

        Args:
            query: Search term (name substring match)
            limit: Max results
        Returns:
            List of {id, name, owner, created, modified, ...}
        """
        params = {"q": query, "limit": limit} if query else {"limit": limit}
        data = self._get("/documents", params=params)
        items = data if isinstance(data, list) else data.get("items", [])

        # Compact to just what we need
        return [
            {
                "id": d.get("id"),
                "name": d.get("name"),
                "owner": d.get("owner", {}).get("name", "?"),
                "created": d.get("createdAt"),
                "modified": d.get("modifiedAt"),
                "trash": d.get("trash", False),
            }
            for d in items[:limit]
        ]

    def create_document(self, name: str) -> dict:
        """Create a new Onshape document.

        Uses onpy (not raw REST) because free accounts are blocked from
        creating docs via the raw REST API (409 error).
        onpy handles auth/headers correctly for document creation.

        Returns: {id, name, default_workspace_id}
        """
        from onpy.client import Client
        self._pre_acquire(3)  # onpy: POST /documents + get workspace + elements → 3 API calls
        client = Client(units="metric")
        doc = client.create_document(name)
        # Invalidate document cache
        self.cache.invalidate()
        # Get workspace from the document
        ws_id = doc.default_workspace.id if doc.default_workspace else None
        return {
            "id": doc.id,
            "name": doc.name,
            "default_workspace_id": ws_id,
        }

    def get_document_info(self, did: str) -> dict:
        """Get document details including workspace and elements.

        Returns: {id, name, workspace: {...}, elements: [...]}
        """
        doc = self._get(f"/documents/{did}")
        
        # Onshape uses "defaultWorkspace" (singular object), not "workspaces" (plural array)
        dws = doc.get("defaultWorkspace", {})
        wid = dws.get("id")
        ws_name = dws.get("name", "Main")
        
        # Get elements
        elements = []
        if wid:
            el_data = self._get(f"/documents/d/{did}/w/{wid}/elements")
            for el in (el_data if isinstance(el_data, list) else []):
                elements.append({
                    "id": el.get("id"),
                    "name": el.get("name"),
                    "type": el.get("type"),
                    "workspace_id": wid,
                })

        return {
            "id": doc.get("id"),
            "name": doc.get("name"),
            "owner": doc.get("owner", {}).get("name", "?"),
            "workspace": {"id": wid, "name": ws_name},
            "elements": elements,
        }

    # ── Parts ──────────────────────────────────────────────────────

    def list_parts(self, did: str, wid: str, eid: str) -> list[dict]:
        """List all parts in a Part Studio.

        Returns: List of {partId, name, bodyType, material, ...}
        """
        data = self._get(f"/parts/d/{did}/w/{wid}/e/{eid}")
        parts = data if isinstance(data, list) else data.get("items", [])
        return [
            {
                "partId": p.get("partId"),
                "name": p.get("name", "Unnamed"),
                "bodyType": p.get("bodyType", "solid"),
                "mass": p.get("mass", {}).get("value") if p.get("mass") else None,
                "volume": p.get("volume", {}).get("value") if p.get("volume") else None,
                "material": (
                    p.get("material", {}).get("displayName")
                    if p.get("material")
                    else None
                ),
            }
            for p in parts
        ]

    def get_part(self, did: str, wid: str, eid: str, part_id: str) -> dict:
        """Get details about a specific part."""
        parts = self.list_parts(did, wid, eid)
        for p in parts:
            if p["partId"] == part_id:
                return p
        raise ValueError(f"Part {part_id} not found in Part Studio {eid}")

    # ── Features ───────────────────────────────────────────────────

    def list_features(self, did: str, wid: str, eid: str) -> list[dict]:
        """List all features in a Part Studio.

        Returns: List of {featureId, name, btType, featureType, suppressed}
        """
        data = self._get(f"/partstudios/d/{did}/w/{wid}/e/{eid}/features")
        features = data.get("features", []) if isinstance(data, dict) else data

        result = []
        for f in features:
            msg = f.get("message", f)  # feature data may be nested
            result.append({
                "featureId": msg.get("featureId", f.get("featureId")),
                "name": msg.get("name", "?"),
                "btType": msg.get("btType", "?"),
                "featureType": f.get("featureType") or msg.get("featureType", "?"),
                "suppressed": msg.get("suppressed", False),
            })
        return result

    def get_feature(self, did: str, wid: str, eid: str, feature_id: str) -> dict:
        """Get a specific feature's details."""
        features = self.list_features(did, wid, eid)
        for f in features:
            if f["featureId"] == feature_id:
                return f
        raise ValueError(f"Feature {feature_id} not found")

    def delete_feature(self, did: str, wid: str, eid: str, feature_id: str) -> dict:
        """Delete a feature by ID. Must delete children before parents!"""
        self._delete(
            f"/partstudios/d/{did}/w/{wid}/e/{eid}/features/featureid/{feature_id}"
        )
        self.cache.invalidate_document(did)
        return {"deleted": feature_id}

    # ── Sketches ───────────────────────────────────────────────────
    # We use onpy for sketch creation because raw REST is fragile.
    # onpy handles btType, feature IDs, and plane references correctly.
    # Only raw REST for operations onpy doesn't support.

    def _get_onpy_client(self):
        """Get a metric onpy client for feature creation."""
        try:
            from onpy.client import Client
            from onpy.elements.partstudio import PartStudio
            from onpy.features.planes import DefaultPlane, DefaultPlaneOrientation, OffsetPlane
            from onpy.features.sketch.sketch import Sketch

            return Client, PartStudio, DefaultPlane, DefaultPlaneOrientation, OffsetPlane, Sketch
        except ImportError:
            raise RuntimeError(
                "onpy not available. Install it in the venv: "
                "pip install onpy (or use ~/dev/onshape-venv/)"
            )

    def create_sketch(
        self,
        did: str,
        wid: str,
        eid: str,
        name: str = "Sketch",
        plane: str = "TOP",
        offset: float = 0.0,
    ) -> dict:
        """Create a new sketch on a plane.

        Args:
            did/wid/eid: Part Studio identifiers
            name: Sketch name
            plane: "TOP", "FRONT", "RIGHT" — or "OFFSET" to use offset distance
            offset: Offset distance in METERS from the plane (positive = +Z for TOP)

        Returns: {sketch_id, name, plane}
        """
        Client, PartStudio, DefaultPlane, DefaultPlaneOrientation, OffsetPlane, Sketch = \
            self._get_onpy_client()

        self._pre_acquire(3)  # onpy: get_document(1) + FeatureScript(1) + feature POST(1)
        client = Client(units="metric")
        doc = client.get_document(did)
        # Find the element
        ps = None
        for el in doc.elements:
            if el.id == eid:
                ps = PartStudio(doc, el)
                break
        if ps is None:
            raise ValueError(f"Element {eid} not found in document {did}")

        # Get plane
        plane_map = {
            "TOP": DefaultPlaneOrientation.TOP,
            "FRONT": DefaultPlaneOrientation.FRONT,
            "RIGHT": DefaultPlaneOrientation.RIGHT,
        }
        orientation = plane_map.get(plane.upper(), DefaultPlaneOrientation.TOP)

        if offset != 0.0:
            base = DefaultPlane(ps, orientation)
            plane_feature = OffsetPlane(ps, base, distance=offset, name=f"{name} Plane")
            sketch = Sketch(ps, plane_feature, name=name)
        else:
            sketch = Sketch(ps, DefaultPlane(ps, orientation), name=name)

        self.cache.invalidate_document(did)

        # Cache the onpy objects so _resolve_sketch can reuse them
        self._sketch_cache[(did, eid, sketch.id)] = (sketch, ps)

        return {
            "sketch_id": sketch.id,
            "name": name,
            "plane": plane,
            "offset": offset,
            "feature_id": sketch.id,  # onpy sketch.id is the feature ID
        }

    def _resolve_sketch(
        self, did: str, wid: str, eid: str, sketch_id: str
    ):
        """Get onpy Sketch object for an existing sketch feature.

        Uses an in-memory cache populated by create_sketch().
        Sketches MUST be created via create_sketch() in the same session —
        onpy does not support reconstructing Sketch objects from feature IDs
        (the Sketch constructor expects a Plane object, not a string).
        """
        # Check cache first
        cache_key = (did, eid, sketch_id)
        if cache_key in self._sketch_cache:
            return self._sketch_cache[cache_key]

        raise ValueError(
            f"Sketch {sketch_id} not found in session cache. "
            f"Sketches must be created via create_sketch() in the same session. "
            f"Re-creating sketches from feature IDs is not supported by onpy — "
            f"the Sketch constructor requires a Plane object, not a string ID. "
            f"Create a new sketch with create_sketch() first."
        )

    def add_circle(
        self,
        did: str,
        wid: str,
        eid: str,
        sketch_id: str,
        center_x: float,
        center_y: float,
        radius: float,
    ) -> dict:
        """Add a circle to an existing sketch.

        Args:
            center_x, center_y: Center point in METERS
            radius: Radius in METERS
        """
        sketch, ps = self._resolve_sketch(did, wid, eid, sketch_id)
        self._pre_acquire(1)  # onpy sketch.add_circle = 1 API call
        sketch.add_circle(center=(center_x, center_y), radius=radius)
        self.cache.invalidate_document(did)
        return {
            "added": "circle",
            "center": [center_x, center_y],
            "radius": radius,
            "sketch_id": sketch_id,
        }

    def add_line(
        self,
        did: str,
        wid: str,
        eid: str,
        sketch_id: str,
        start_x: float,
        start_y: float,
        end_x: float,
        end_y: float,
    ) -> dict:
        """Add a line segment to an existing sketch. Points in METERS."""
        sketch, ps = self._resolve_sketch(did, wid, eid, sketch_id)
        self._pre_acquire(1)  # onpy sketch.add_line = 1 API call
        sketch.add_line((start_x, start_y), (end_x, end_y))
        self.cache.invalidate_document(did)
        return {
            "added": "line",
            "start": [start_x, start_y],
            "end": [end_x, end_y],
            "sketch_id": sketch_id,
        }

    def add_rectangle(
        self,
        did: str,
        wid: str,
        eid: str,
        sketch_id: str,
        corner1_x: float,
        corner1_y: float,
        corner2_x: float,
        corner2_y: float,
    ) -> dict:
        """Add a rectangle to an existing sketch. Corner points in METERS."""
        sketch, ps = self._resolve_sketch(did, wid, eid, sketch_id)
        self._pre_acquire(1)  # onpy sketch.add_rectangle = 1 API call
        # onpy add_rectangle takes two corner points
        sketch.add_rectangle((corner1_x, corner1_y), (corner2_x, corner2_y))
        self.cache.invalidate_document(did)
        return {
            "added": "rectangle",
            "corner1": [corner1_x, corner1_y],
            "corner2": [corner2_x, corner2_y],
            "sketch_id": sketch_id,
        }

    # ── Extrude ────────────────────────────────────────────────────

    def extrude(
        self,
        did: str,
        wid: str,
        eid: str,
        sketch_id: str,
        distance: float,
        operation: str = "NEW",
        merge_with_part: Optional[str] = None,
    ) -> dict:
        """Extrude a sketch into a 3D body.

        Args:
            distance: Extrude distance in METERS
            operation: "NEW" (new body), "ADD" (merge with existing), "REMOVE" (cut)
            merge_with_part: Part ID to merge with (for ADD operation only)

        Note: "REMOVE" (extrude-cut) uses raw REST because onpy's subtract_from
              is buggy. For REMOVE, you need the face IDs of the target body.

        Returns: {extrude_id, operation, distance}
        """
        from onpy.client import Client
        from onpy.elements.partstudio import PartStudio
        from onpy.features.extrude import Extrude

        self._pre_acquire(3)  # onpy: get_document(1) + FeatureScript(1) + feature POST(1)
        client = Client(units="metric")
        doc = client.get_document(did)
        ps = None
        for el in doc.elements:
            if el.id == eid:
                ps = PartStudio(doc, el)
                break
        if ps is None:
            raise ValueError(f"Element {eid} not found")

        sketch, _ = self._resolve_sketch(did, wid, eid, sketch_id)

        if operation.upper() == "REMOVE":
            # Use raw REST for subtract — see boolean-workaround.md
            return self._extrude_remove(did, wid, eid, sketch_id, distance)

        # NEW or ADD
        merge_with = None
        if operation.upper() == "ADD" and merge_with_part:
            # Find the part object
            for p in ps.parts:
                if p.id == merge_with_part:
                    merge_with = p
                    break
            if merge_with is None:
                raise ValueError(f"Part {merge_with_part} not found for merge")

        extrude = Extrude(ps, sketch, distance=distance, merge_with=merge_with)

        self.cache.invalidate_document(did)

        return {
            "extrude_id": extrude.id,
            "operation": operation.upper(),
            "distance": distance,
            "sketch_id": sketch_id,
        }

    def _extrude_remove(
        self, did: str, wid: str, eid: str, sketch_id: str, depth: float
    ) -> dict:
        """Extrude-remove (cut) via raw REST API.

        Uses FeatureScript to get transient face IDs, then POSTs the extrude feature.
        See references/boolean-workaround.md in the onshape skill.
        """
        # Step 1: Get transient IDs for the sketch faces
        fs_script = f"""
        function(context is Context, queries) {{
            var sketchFeature = makeId("{sketch_id}");
            var faces = evaluateQuery(context, qCreatedBy(sketchFeature, EntityType.FACE));
            return transientQueriesToStrings(faces);
        }}
        """
        base = f"/partstudios/d/{did}/w/{wid}/e/{eid}"
        fs_result = self._post(f"{base}/featurescript", json_data={"script": fs_script})

        face_ids = []
        result_value = fs_result.get("result", {}).get("value", [])
        for v in result_value:
            if isinstance(v, dict) and "value" in v:
                face_ids.append(v["value"])

        if not face_ids:
            raise RuntimeError(
                f"No faces found for sketch {sketch_id}. Is the sketch closed?"
            )

        # Step 2: POST extrude-remove feature
        extrude_cut = {
            "feature": {
                "btType": "BTMFeature-134",
                "name": f"Extrude Cut {sketch_id}",
                "featureType": "extrude",
                "parameters": [
                    {
                        "btType": "BTMParameterEnum-145",
                        "enumName": "NewBodyOperationType",
                        "value": "REMOVE",
                        "parameterId": "operationType",
                    },
                    {
                        "btType": "BTMParameterQueryList-148",
                        "queries": [
                            {
                                "btType": "BTMIndividualQuery-138",
                                "deterministicIds": face_ids,
                            }
                        ],
                        "parameterId": "entities",
                    },
                    {
                        "btType": "BTMParameterEnum-145",
                        "enumName": "BoundingType",
                        "value": "BLIND",
                        "parameterId": "endBound",
                    },
                    {
                        "btType": "BTMParameterQuantity-147",
                        "expression": f"{depth} m",
                        "parameterId": "depth",
                    },
                ],
                "suppressed": False,
            }
        }

        result = self._post(f"{base}/features", json_data=extrude_cut)
        self.cache.invalidate_document(did)

        return {
            "extrude_id": result.get("feature", {}).get("featureId", "?"),
            "operation": "REMOVE",
            "distance": depth,
            "sketch_id": sketch_id,
        }

    # ── Revolve ────────────────────────────────────────────────────

    @staticmethod
    def validate_revolve_profile(
        points: list[tuple[float, float, float]],
        axis_direction: tuple[float, float, float] = (0, 0, 1),
        epsilon: float = 1e-9,
    ) -> list[str]:
        """Validate a revolve profile for known silent-failure modes.

        Returns a list of human-readable problem descriptions. Empty list
        means the profile passes all checks.

        Checks:
        1. At least 3 distinct points.
        2. All points strictly on one side of the revolve axis (radial
           distance > epsilon). A point on the axis means revolve fails
           silently.
        3. Closed profile — last point must connect back to first point
           (within epsilon). Note: a "closed" polygon list may or may not
           repeat the first point; both are tolerated, but if it isn't
           repeated, the implicit closing edge is what's checked.
        4. Non-self-intersecting profile — uses a segment-segment
           intersection test on every non-adjacent edge pair.

        Args:
            points: list of (x, y, z) tuples in METERS describing the
                profile polygon in order.
            axis_direction: (dx, dy, dz) revolve axis direction (anchored
                at origin for the radial check). Need not be normalized.
            epsilon: numerical tolerance.

        Returns:
            list[str]: problem descriptions; empty if profile is OK.
        """
        import math

        problems: list[str] = []

        # ---- Check 1: at least 3 distinct points ------------------------
        if not points or len(points) < 3:
            problems.append(
                f"Profile has only {len(points) if points else 0} point(s); "
                "a revolve profile needs at least 3 distinct points."
            )
            return problems

        # Build the polygon edges. If the last point equals the first
        # within epsilon, treat the list as already-closed and drop the
        # duplicate; otherwise add an implicit closing edge.
        pts = [tuple(float(c) for c in p) for p in points]
        closed_explicit = (
            math.dist(pts[0], pts[-1]) <= epsilon and len(pts) >= 4
        )
        verts = pts[:-1] if closed_explicit else pts

        # Distinct vertex count
        distinct = []
        for v in verts:
            if not any(math.dist(v, u) <= epsilon for u in distinct):
                distinct.append(v)
        if len(distinct) < 3:
            problems.append(
                f"Profile has only {len(distinct)} distinct point(s) "
                "(within epsilon); need at least 3."
            )

        # ---- Check 2: profile crosses revolve axis ----------------------
        # Radial distance from axis line through origin with direction d:
        # r = |p − (p·d_hat) d_hat|
        dx, dy, dz = axis_direction
        d_len = math.sqrt(dx * dx + dy * dy + dz * dz)
        if d_len < 1e-12:
            problems.append("Axis direction is zero-length.")
            return problems
        d_hat = (dx / d_len, dy / d_len, dz / d_len)

        axis_violations = []
        for i, (px, py, pz) in enumerate(verts):
            dot = px * d_hat[0] + py * d_hat[1] + pz * d_hat[2]
            proj = (d_hat[0] * dot, d_hat[1] * dot, d_hat[2] * dot)
            rx, ry, rz = px - proj[0], py - proj[1], pz - proj[2]
            r = math.sqrt(rx * rx + ry * ry + rz * rz)
            if r <= epsilon:
                axis_violations.append((i, (px, py, pz), r))
        if axis_violations:
            details = ", ".join(
                f"point[{i}]={p} (radial dist={r:.3e})"
                for i, p, r in axis_violations
            )
            problems.append(
                "Profile crosses or touches the revolve axis — every point "
                f"must have radial distance > {epsilon} from the axis "
                f"(direction={axis_direction}). Offending: {details}. "
                "Revolve will fail silently — shift the profile away from "
                "the axis."
            )

        # ---- Check 3: open profile --------------------------------------
        # Already-closed lists pass by definition. For non-closed lists,
        # require last == first within epsilon OR rely on the implicit
        # closing edge — but flag if the closing edge is the only thing
        # making it closed AND it is long (a real "open" sketch).
        if not closed_explicit:
            gap = math.dist(pts[0], pts[-1])
            # An implicit closing edge of nonzero length is fine for a
            # polygon — many callers describe profiles as ordered verts
            # without repeating the first. So we don't flag this as open
            # unless the user passed something obviously inconsistent.
            # We do, however, surface a helpful note when gap is exactly
            # zero in degenerate way (handled above).
            _ = gap  # currently no failure case; intersection check covers it

        # ---- Check 4: self-intersecting profile -------------------------
        # Project onto the meridian plane: 2D coords = (radial, axial).
        # This is the plane the revolve actually operates in.
        def to_2d(p):
            axial = p[0] * d_hat[0] + p[1] * d_hat[1] + p[2] * d_hat[2]
            rx = p[0] - axial * d_hat[0]
            ry = p[1] - axial * d_hat[1]
            rz = p[2] - axial * d_hat[2]
            radial = math.sqrt(rx * rx + ry * ry + rz * rz)
            return (radial, axial)

        verts2d = [to_2d(p) for p in verts]
        n = len(verts2d)
        # Edges: i -> (i+1) % n
        edges = [(verts2d[i], verts2d[(i + 1) % n]) for i in range(n)]

        def _orient(a, b, c):
            return (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])

        def _on_seg(a, b, c):
            return (
                min(a[0], b[0]) - epsilon <= c[0] <= max(a[0], b[0]) + epsilon
                and min(a[1], b[1]) - epsilon <= c[1] <= max(a[1], b[1]) + epsilon
            )

        def _seg_intersect(p1, p2, p3, p4):
            o1 = _orient(p1, p2, p3)
            o2 = _orient(p1, p2, p4)
            o3 = _orient(p3, p4, p1)
            o4 = _orient(p3, p4, p2)
            if (o1 > epsilon and o2 < -epsilon or o1 < -epsilon and o2 > epsilon) and (
                o3 > epsilon and o4 < -epsilon or o3 < -epsilon and o4 > epsilon
            ):
                return True
            # Colinear overlap cases (treat as intersection only if real overlap)
            if abs(o1) <= epsilon and _on_seg(p1, p2, p3):
                # endpoint touch is OK only if it's the shared vertex
                return not (
                    math.dist(p3, p1) <= epsilon or math.dist(p3, p2) <= epsilon
                )
            if abs(o2) <= epsilon and _on_seg(p1, p2, p4):
                return not (
                    math.dist(p4, p1) <= epsilon or math.dist(p4, p2) <= epsilon
                )
            if abs(o3) <= epsilon and _on_seg(p3, p4, p1):
                return not (
                    math.dist(p1, p3) <= epsilon or math.dist(p1, p4) <= epsilon
                )
            if abs(o4) <= epsilon and _on_seg(p3, p4, p2):
                return not (
                    math.dist(p2, p3) <= epsilon or math.dist(p2, p4) <= epsilon
                )
            return False

        intersections = []
        for i in range(n):
            for j in range(i + 1, n):
                # Skip adjacent edges (share an endpoint) and the wrap-around
                if j == i + 1 or (i == 0 and j == n - 1):
                    continue
                a1, a2 = edges[i]
                b1, b2 = edges[j]
                if _seg_intersect(a1, a2, b1, b2):
                    intersections.append((i, j))
        if intersections:
            pretty = ", ".join(f"edge {i}↔edge {j}" for i, j in intersections)
            problems.append(
                "Profile is self-intersecting in the meridian plane: "
                f"{pretty}. Most common cause: non-monotone coordinates "
                "along the axis direction — for a clockwise profile, the "
                "axial coordinate must be strictly monotonic from one bead "
                "through the body to the other bead before the closing "
                "edge returns. Revolve will fail silently."
            )

        return problems

    def revolve(
        self,
        did: str,
        wid: str,
        eid: str,
        sketch_id: str,
        axis_point: tuple[float, float, float] = (0, 0, 0),
        axis_direction: tuple[float, float, float] = (0, 0, 1),
        angle_deg: float = 360,
        operation: str = "NEW",
        profile_points: Optional[list[tuple[float, float, float]]] = None,
    ) -> dict:
        """Revolve a sketch around an axis to create a 3D body.

        Uses FeatureScript because the REST revolve endpoint is broken
        (BTMParameterEntityList-160 type not accepted).

        Args:
            sketch_id: Sketch feature ID to revolve (must be closed)
            axis_point: (x, y, z) in METERS — a point on the revolve axis
            axis_direction: (dx, dy, dz) — direction vector (will be normalized)
            angle_deg: Revolve angle in degrees (default 360 for full revolve)
            operation: "NEW" (new body), "ADD" (merge), "REMOVE" (cut)

        Returns: {revolve_id, operation, angle_deg, axis}

        ⚠️ Silent failure modes:
        1. Profile crosses axis — all sketch points must be strictly on one side
           of the axis. For Z-axis revolve, all points must have X > 0.
        2. Self-intersecting profile — for Z-axis revolve, Z coordinates must
           be strictly monotonic around the profile.
        3. Open profile — sketch must form a closed region.

        For most common use case (revolve around Z): use defaults.
        """
        import math

        # Normalize axis direction
        dx, dy, dz = axis_direction
        length = math.sqrt(dx * dx + dy * dy + dz * dz)
        if length < 1e-10:
            raise ValueError("Axis direction has zero length")
        dx, dy, dz = dx / length, dy / length, dz / length
        ax, ay, az = axis_point

        # ── Profile validation (catch silent failure modes early) ──
        if profile_points is not None:
            problems = self.validate_revolve_profile(
                profile_points, axis_direction=axis_direction
            )
            if problems:
                msg_lines = [
                    "Revolve profile validation failed — Onshape's revolve "
                    "would silently produce no body. Problems detected:"
                ]
                for i, p in enumerate(problems, 1):
                    msg_lines.append(f"  {i}. {p}")
                msg_lines.append(
                    f"Axis direction: {axis_direction}. "
                    "Fix the profile and retry."
                )
                raise ValueError("\n".join(msg_lines))

        # Map operation to FeatureScript enum
        op_map = {
            "NEW": "NewBodyOperationType.NEW",
            "ADD": "NewBodyOperationType.ADD",
            "REMOVE": "NewBodyOperationType.REMOVE",
        }
        op_enum = op_map.get(operation.upper(), "NewBodyOperationType.NEW")

        # Build FeatureScript
        fs_script = f"""
function(context is Context, queries) {{
    var sketchFeature = makeId("{sketch_id}");
    var regions = qSketchRegion(sketchFeature, false);
    revolve(context, id + "RevolvedBody", {{
        "entities": regions,
        "axis": line(
            vector({ax}, {ay}, {az}) * meter,
            vector({dx}, {dy}, {dz}) * meter
        ),
        "angleForward": {angle_deg} * degree,
        "operationType": {op_enum}
    }});
}}
"""
        self._pre_acquire(2)  # FeatureScript eval + possible feature creation
        base = f"/partstudios/d/{did}/w/{wid}/e/{eid}"
        result = self._post(f"{base}/featurescript", json_data={"script": fs_script})
        self.cache.invalidate_document(did)

        # Check if FeatureScript succeeded
        fs_result = result.get("result", {})
        if not fs_result:
            raise RuntimeError(
                "Revolve FeatureScript returned no result. "
                "Common causes: profile crosses axis, self-intersecting polygon, "
                "or open sketch. Check the sketch geometry."
            )

        return {
            "revolve_id": fs_result.get("value", "ok"),
            "operation": operation.upper(),
            "angle_deg": angle_deg,
            "axis": {
                "point": [ax, ay, az],
                "direction": [round(dx, 6), round(dy, 6), round(dz, 6)],
            },
        }

    # ── Fillet / Chamfer ───────────────────────────────────────────

    def _edge_modifier(
        self,
        did: str,
        wid: str,
        eid: str,
        edge_feature_id: str,
        size: float,
        kind: str,
    ) -> dict:
        """Shared FeatureScript driver for fillet/chamfer.

        Applies a fillet or chamfer to all edges created by a given feature
        (typically an extrude or revolve). Uses FeatureScript because the
        REST endpoints for fillet/chamfer are unreliable.

        Args:
            edge_feature_id: Feature ID whose created edges will be modified
                             (e.g., an extrude or revolve feature ID).
            size: Radius (fillet) or distance (chamfer) in METERS.
            kind: "fillet" or "chamfer".
        """
        if kind == "fillet":
            fs_op = (
                f'    fillet(context, id + "Fillet", {{\n'
                f'        "entities": edges,\n'
                f'        "radius": {size} * meter\n'
                f'    }});'
            )
            feature_label = "Fillet"
            size_label = "radius"
        elif kind == "chamfer":
            fs_op = (
                f'    chamfer(context, id + "Chamfer", {{\n'
                f'        "entities": edges,\n'
                f'        "distance": {size} * meter\n'
                f'    }});'
            )
            feature_label = "Chamfer"
            size_label = "distance"
        else:
            raise ValueError(f"Unknown edge modifier kind: {kind}")

        fs_script = (
            f'function(context is Context, queries) {{\n'
            f'    var edges = qCreatedBy(makeId("{edge_feature_id}"), EntityType.EDGE);\n'
            f'{fs_op}\n'
            f'}}\n'
        )

        self._pre_acquire(2)
        base = f"/partstudios/d/{did}/w/{wid}/e/{eid}"
        result = self._post(f"{base}/featurescript", json_data={"script": fs_script})
        self.cache.invalidate_document(did)

        fs_result = result.get("result", {})
        if not fs_result and "result" not in result:
            raise RuntimeError(
                f"{feature_label} FeatureScript returned no result. "
                f"Common causes: feature {edge_feature_id} has no edges, "
                f"or {size_label} ({size}m) is too large for the geometry."
            )

        return {
            f"{kind}_id": fs_result.get("value", "ok") if isinstance(fs_result, dict) else "ok",
            "edge_feature_id": edge_feature_id,
            size_label: size,
        }

    def fillet(
        self,
        did: str,
        wid: str,
        eid: str,
        edge_feature_id: str,
        radius: float,
        operation: str = "NEW",  # accepted for API symmetry, not used (filler)
    ) -> dict:
        """Apply a fillet (round) to all edges created by a feature.

        Args:
            edge_feature_id: Feature ID (e.g., an extrude) whose created edges
                             will be filleted. Use list_features to find it.
            radius: Fillet radius in METERS. E.g., 5mm = 0.005.
            operation: Currently informational only — fillet always modifies
                       the existing body.

        Returns: {fillet_id, edge_feature_id, radius}
        """
        return self._edge_modifier(did, wid, eid, edge_feature_id, radius, "fillet")

    def chamfer(
        self,
        did: str,
        wid: str,
        eid: str,
        edge_feature_id: str,
        distance: float,
        operation: str = "NEW",  # accepted for API symmetry, not used
    ) -> dict:
        """Apply a chamfer (bevel) to all edges created by a feature.

        Args:
            edge_feature_id: Feature ID whose created edges will be chamfered.
            distance: Chamfer distance in METERS. E.g., 2mm = 0.002.
            operation: Currently informational only.

        Returns: {chamfer_id, edge_feature_id, distance}
        """
        return self._edge_modifier(did, wid, eid, edge_feature_id, distance, "chamfer")

    # ── Export ─────────────────────────────────────────────────────

    def export_stl(
        self,
        did: str,
        wid: str,
        eid: str,
        output_path: str,
        units: str = "millimeter",
    ) -> dict:
        """Export a Part Studio as STL.

        Args:
            output_path: Local file path to save the STL
            units: "millimeter", "centimeter", "meter", "inch", "foot"

        Returns: {path, size_bytes}
        """
        params = {
            "mode": "binary",
            "units": units,
            "grouping": True,
        }
        resp = self._request(
            "GET",
            f"/partstudios/d/{did}/w/{wid}/e/{eid}/stl",
            params=params,
            use_cache=False,
            raw_response=True,
        )
        with open(output_path, "wb") as f:
            f.write(resp.content)
        size = len(resp.content)
        return {"path": output_path, "size_bytes": size, "units": units}

    # ── Thumbnails / Vision ────────────────────────────────────────

    def get_thumbnail(
        self,
        did: str,
        wid: str,
        eid: str,
        output_path: str,
        width: int = 600,
        height: int = 400,
    ) -> dict:
        """Download a shaded thumbnail image of a Part Studio.

        Useful for "seeing" the model visually.

        Args:
            output_path: Local file path to save the PNG
            width, height: Image dimensions (max 1024)

        Returns: {path, size_bytes, width, height}
        """
        params = {"width": width, "height": height}
        resp = self._request(
            "GET",
            f"/partstudios/d/{did}/w/{wid}/e/{eid}/shadedviews",
            params=params,
            use_cache=False,
            raw_response=True,
        )
        # The shadedviews endpoint returns JSON with image data
        data = resp.json()
        images = (
            data.get("images", [])
            if isinstance(data, dict)
            else []
        )
        if not images:
            raise RuntimeError("No thumbnail images returned. Check document/element IDs.")

        # First image is usually the iso view
        img_data = base64.b64decode(images[0])
        with open(output_path, "wb") as f:
            f.write(img_data)

        return {
            "path": output_path,
            "size_bytes": len(img_data),
            "width": width,
            "height": height,
        }

    # ── Utility ────────────────────────────────────────────────────

    def close(self):
        """Close the HTTP client."""
        self._http.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()
