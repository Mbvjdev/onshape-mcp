"""Onshape MCP server — semantic CAD tools for Claude/DeepSeek.

Provides 15+ tools that wrap the Onshape API in natural-language-friendly operations:
- Document management: list, create, get info
- Parts: list, get details
- Features: list, get, delete
- Sketching: create sketches, add circles/lines/rectangles
- 3D: extrude (new/add/remove)
- Export: STL
- Vision: thumbnail/shaded views

The server handles rate limiting, caching, and auth transparently.
Built from hard-won experience with Onshape's API (see onshape skill).

Usage:
    python -m onshape_mcp.server
    # or via entry point:
    onshape-mcp
"""

import asyncio
import json
import logging
import sys
from pathlib import Path
from typing import Any

# Check for mcp SDK
try:
    from mcp.server import Server
    from mcp.server.stdio import stdio_server
    from mcp.types import Tool, TextContent, ImageContent
except ImportError:
    print(
        "MCP SDK not installed. Run: pip install mcp",
        file=sys.stderr,
    )
    sys.exit(1)

from .client import OnshapeClient, MM, CM, M

logging.basicConfig(
    level=logging.WARNING,  # WARNING to avoid noise in MCP stdio
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    stream=sys.stderr,  # stderr is safe — MCP uses stdout for protocol
)
logger = logging.getLogger("onshape-mcp")

# Global client — initialized once at startup
_client: OnshapeClient | None = None


def get_client() -> OnshapeClient:
    global _client
    if _client is None:
        _client = OnshapeClient()
    return _client


# ── Tool Definitions ────────────────────────────────────────────

TOOLS = [
    Tool(
        name="list_documents",
        description=(
            "Search and list Onshape documents. "
            "Returns document names, IDs, and owners. "
            "Use this to find a document before working with its contents."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Search term — matches document names (substring). Leave empty to list recent documents.",
                },
                "limit": {
                    "type": "integer",
                    "description": "Maximum number of results (default: 20, max: 50)",
                    "default": 20,
                },
            },
            "required": [],
        },
    ),
    Tool(
        name="create_document",
        description="Create a new Onshape document. Returns the document ID and workspace ID needed for subsequent operations.",
        inputSchema={
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "Document name. Must be unique in your account.",
                },
            },
            "required": ["name"],
        },
    ),
    Tool(
        name="get_document_info",
        description=(
            "Get detailed information about an Onshape document, including its workspaces "
            "and elements (Part Studios, Assemblies, etc.). Use this to find the element IDs "
            "needed for working with parts and features."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "did": {
                    "type": "string",
                    "description": "Document ID (24-character hex string)",
                },
            },
            "required": ["did"],
        },
    ),
    Tool(
        name="list_parts",
        description=(
            "List all parts in a Part Studio. Returns part names, IDs, body types (solid/surface), "
            "mass, volume, and material. Use this to inspect what's in a Part Studio."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "did": {
                    "type": "string",
                    "description": "Document ID",
                },
                "wid": {
                    "type": "string",
                    "description": "Workspace ID",
                },
                "eid": {
                    "type": "string",
                    "description": "Element ID (Part Studio)",
                },
            },
            "required": ["did", "wid", "eid"],
        },
    ),
    Tool(
        name="list_features",
        description=(
            "List all features in a Part Studio with their types, names, and suppression status. "
            "Features are the building blocks: sketches, extrudes, revolves, fillets, etc. "
            "Use this to understand the feature tree before modifying anything."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "did": {"type": "string", "description": "Document ID"},
                "wid": {"type": "string", "description": "Workspace ID"},
                "eid": {"type": "string", "description": "Element ID (Part Studio)"},
            },
            "required": ["did", "wid", "eid"],
        },
    ),
    Tool(
        name="get_feature_info",
        description="Get detailed information about a specific feature by its ID.",
        inputSchema={
            "type": "object",
            "properties": {
                "did": {"type": "string", "description": "Document ID"},
                "wid": {"type": "string", "description": "Workspace ID"},
                "eid": {"type": "string", "description": "Element ID (Part Studio)"},
                "feature_id": {
                    "type": "string",
                    "description": "Feature ID from list_features",
                },
            },
            "required": ["did", "wid", "eid", "feature_id"],
        },
    ),
    Tool(
        name="delete_feature",
        description=(
            "Delete a feature from a Part Studio. "
            "⚠️ IMPORTANT: Features must be deleted in reverse order — "
            "children before parents. For example, delete an extrude before its sketch, "
            "and delete later features before earlier ones."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "did": {"type": "string", "description": "Document ID"},
                "wid": {"type": "string", "description": "Workspace ID"},
                "eid": {"type": "string", "description": "Element ID (Part Studio)"},
                "feature_id": {
                    "type": "string",
                    "description": "Feature ID to delete (from list_features)",
                },
            },
            "required": ["did", "wid", "eid", "feature_id"],
        },
    ),
    Tool(
        name="create_sketch",
        description=(
            "Create a new 2D sketch on a plane in a Part Studio. "
            "This is the first step for creating 3D geometry. "
            "After creating a sketch, use add_circle, add_line, or add_rectangle "
            "to draw geometry, then use extrude to turn it into a 3D body.\n\n"
            "Planes: TOP (XY plane), FRONT (XZ plane), RIGHT (YZ plane).\n"
            "Add an offset in meters to create the sketch above/below the plane.\n\n"
            "UNITS: All distances are in METERS. Convert: 1 mm = 0.001, 1 cm = 0.01.\n"
            "Example: 50mm offset from TOP → offset=0.05"
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "did": {"type": "string", "description": "Document ID"},
                "wid": {"type": "string", "description": "Workspace ID"},
                "eid": {"type": "string", "description": "Element ID (Part Studio)"},
                "name": {
                    "type": "string",
                    "description": "Sketch name (e.g., 'Bolt Pattern', 'Rim Profile')",
                },
                "plane": {
                    "type": "string",
                    "enum": ["TOP", "FRONT", "RIGHT"],
                    "description": "Which standard plane to sketch on",
                    "default": "TOP",
                },
                "offset": {
                    "type": "number",
                    "description": "Offset distance in METERS from the plane. Positive = away from origin.",
                    "default": 0.0,
                },
            },
            "required": ["did", "wid", "eid", "name"],
        },
    ),
    Tool(
        name="add_circle",
        description=(
            "Add a circle to an existing sketch. The sketch must have been created "
            "with create_sketch first. Multiple circles in the same sketch are supported.\n\n"
            "UNITS: All coordinates and dimensions in METERS. 1mm = 0.001, 1cm = 0.01.\n"
            "Example: Circle at origin, Ø10mm → center=(0,0), radius=0.005"
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "did": {"type": "string", "description": "Document ID"},
                "wid": {"type": "string", "description": "Workspace ID"},
                "eid": {"type": "string", "description": "Element ID (Part Studio)"},
                "sketch_id": {
                    "type": "string",
                    "description": "Sketch feature ID (from create_sketch or list_features)",
                },
                "center_x": {
                    "type": "number",
                    "description": "X coordinate of circle center in METERS",
                },
                "center_y": {
                    "type": "number",
                    "description": "Y coordinate of circle center in METERS",
                },
                "radius": {
                    "type": "number",
                    "description": "Circle radius in METERS (Ø/2). E.g., Ø10mm → 0.005",
                },
            },
            "required": ["did", "wid", "eid", "sketch_id", "center_x", "center_y", "radius"],
        },
    ),
    Tool(
        name="add_line",
        description=(
            "Add a line segment to an existing sketch. Connect multiple lines to form polygons. "
            "For a closed polygon, make sure the last line ends where the first line started.\n\n"
            "UNITS: Coordinates in METERS. 1mm = 0.001.\n"
            "⚠️ Rate limit warning: Each add_line call is one API request. "
            "Complex polygons (10+ lines) may hit rate limits. "
            "For complex profiles, create the sketch and let the user add lines manually."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "did": {"type": "string", "description": "Document ID"},
                "wid": {"type": "string", "description": "Workspace ID"},
                "eid": {"type": "string", "description": "Element ID (Part Studio)"},
                "sketch_id": {
                    "type": "string",
                    "description": "Sketch feature ID",
                },
                "start_x": {"type": "number", "description": "Start X coordinate in METERS"},
                "start_y": {"type": "number", "description": "Start Y coordinate in METERS"},
                "end_x": {"type": "number", "description": "End X coordinate in METERS"},
                "end_y": {"type": "number", "description": "End Y coordinate in METERS"},
            },
            "required": [
                "did", "wid", "eid", "sketch_id",
                "start_x", "start_y", "end_x", "end_y",
            ],
        },
    ),
    Tool(
        name="add_rectangle",
        description=(
            "Add a rectangle to an existing sketch. "
            "Specify two opposite corners — the rectangle is aligned with the sketch axes.\n\n"
            "UNITS: Coordinates in METERS. 1mm = 0.001."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "did": {"type": "string", "description": "Document ID"},
                "wid": {"type": "string", "description": "Workspace ID"},
                "eid": {"type": "string", "description": "Element ID (Part Studio)"},
                "sketch_id": {"type": "string", "description": "Sketch feature ID"},
                "corner1_x": {"type": "number", "description": "First corner X in METERS"},
                "corner1_y": {"type": "number", "description": "First corner Y in METERS"},
                "corner2_x": {"type": "number", "description": "Opposite corner X in METERS"},
                "corner2_y": {"type": "number", "description": "Opposite corner Y in METERS"},
            },
            "required": [
                "did", "wid", "eid", "sketch_id",
                "corner1_x", "corner1_y", "corner2_x", "corner2_y",
            ],
        },
    ),
    Tool(
        name="extrude",
        description=(
            "Extrude a sketch into a 3D solid body. This turns 2D geometry into 3D.\n\n"
            "Operations:\n"
            "- NEW: Create a new independent body (default)\n"
            "- ADD: Merge with an existing body (requires merge_with_part_id)\n"
            "- REMOVE: Cut material away (subtract from existing bodies)\n\n"
            "UNITS: Distance in METERS. E.g., extrude 5mm → distance=0.005.\n\n"
            "⚠️ REMOVE operation may fail on complex geometry — in that case, "
            "switch to sketches-only mode and let the user cut manually in Onshape UI."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "did": {"type": "string", "description": "Document ID"},
                "wid": {"type": "string", "description": "Workspace ID"},
                "eid": {"type": "string", "description": "Element ID (Part Studio)"},
                "sketch_id": {
                    "type": "string",
                    "description": "Sketch feature ID to extrude",
                },
                "distance": {
                    "type": "number",
                    "description": "Extrude distance in METERS. Positive = away from sketch plane. E.g., 5mm = 0.005",
                },
                "operation": {
                    "type": "string",
                    "enum": ["NEW", "ADD", "REMOVE"],
                    "description": "Operation type: NEW body, ADD to existing, or REMOVE (cut)",
                    "default": "NEW",
                },
                "merge_with_part_id": {
                    "type": "string",
                    "description": "Part ID to merge with (required for ADD operation). From list_parts.",
                },
            },
            "required": ["did", "wid", "eid", "sketch_id", "distance"],
        },
    ),
    Tool(
        name="revolve",
        description=(
            "Revolve a sketch around an axis to create a 3D body. "
            "This is how you create round/turned parts like cylinders, wheels, "
            "tires, shafts, etc. from a 2D profile.\n\n"
            "UNITS: All coordinates in METERS. Angle in degrees.\n"
            "Default axis is Z (0,0,0)→(0,0,1) which revolves around Z.\n\n"
            "IMPORTANT: The sketch must be a closed profile that does NOT cross "
            "the revolve axis. For a Z-axis revolve, all sketch points must have X > 0.\n\n"
            "Common patterns:\n"
            "- Full 360° revolve around Z: use defaults\n"
            "- Revolve around a vertical line at X=0.05: axis_point=(0.05, 0, 0)\n"
            "- Revolve 90° sector: angle_deg=90\n\n"
            "⚠️ Silent failure: If no body is created, check that the sketch profile "
            "doesn't cross the axis and isn't self-intersecting."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "did": {"type": "string", "description": "Document ID"},
                "wid": {"type": "string", "description": "Workspace ID"},
                "eid": {"type": "string", "description": "Element ID (Part Studio)"},
                "sketch_id": {
                    "type": "string",
                    "description": "Sketch feature ID to revolve",
                },
                "axis_point_x": {
                    "type": "number",
                    "description": "X coordinate of a point on the revolve axis in METERS (default 0)",
                    "default": 0,
                },
                "axis_point_y": {
                    "type": "number",
                    "description": "Y coordinate of a point on the revolve axis in METERS (default 0)",
                    "default": 0,
                },
                "axis_point_z": {
                    "type": "number",
                    "description": "Z coordinate of a point on the revolve axis in METERS (default 0)",
                    "default": 0,
                },
                "axis_dir_x": {
                    "type": "number",
                    "description": "X component of axis direction (default 0)",
                    "default": 0,
                },
                "axis_dir_y": {
                    "type": "number",
                    "description": "Y component of axis direction (default 0)",
                    "default": 0,
                },
                "axis_dir_z": {
                    "type": "number",
                    "description": "Z component of axis direction (default 1 for Z-axis)",
                    "default": 1,
                },
                "angle_deg": {
                    "type": "number",
                    "description": "Revolve angle in degrees (default 360 for full revolve)",
                    "default": 360,
                },
                "operation": {
                    "type": "string",
                    "enum": ["NEW", "ADD", "REMOVE"],
                    "description": "Operation type: NEW body, ADD to existing, or REMOVE (cut)",
                    "default": "NEW",
                },
            },
            "required": ["did", "wid", "eid", "sketch_id"],
        },
    ),
    Tool(
        name="fillet",
        description=(
            "Add a fillet (round) to all edges created by a feature (typically an extrude or revolve). "
            "Uses FeatureScript under the hood — robust against the REST endpoint's quirks.\n\n"
            "UNITS: radius in METERS. E.g., 5mm fillet → radius=0.005.\n\n"
            "TIP: Pass the feature_id of the extrude/revolve whose edges you want rounded. "
            "All edges created by that feature will be filleted. "
            "If the radius is too large for the geometry, the operation fails silently."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "did": {"type": "string", "description": "Document ID"},
                "wid": {"type": "string", "description": "Workspace ID"},
                "eid": {"type": "string", "description": "Element ID (Part Studio)"},
                "feature_id": {
                    "type": "string",
                    "description": "Feature ID whose edges will be filleted (e.g., an extrude feature ID from list_features).",
                },
                "radius": {
                    "type": "number",
                    "description": "Fillet radius in METERS. E.g., 5mm = 0.005.",
                },
                "operation": {
                    "type": "string",
                    "enum": ["NEW", "ADD", "REMOVE"],
                    "description": "Reserved for API symmetry — fillet always modifies the existing body.",
                    "default": "NEW",
                },
            },
            "required": ["did", "wid", "eid", "feature_id", "radius"],
        },
    ),
    Tool(
        name="chamfer",
        description=(
            "Add a chamfer (bevel) to all edges created by a feature. "
            "Uses FeatureScript under the hood.\n\n"
            "UNITS: distance in METERS. E.g., 2mm chamfer → distance=0.002.\n\n"
            "TIP: Pass the feature_id of the extrude/revolve whose edges you want beveled. "
            "All edges created by that feature will be chamfered."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "did": {"type": "string", "description": "Document ID"},
                "wid": {"type": "string", "description": "Workspace ID"},
                "eid": {"type": "string", "description": "Element ID (Part Studio)"},
                "feature_id": {
                    "type": "string",
                    "description": "Feature ID whose edges will be chamfered.",
                },
                "distance": {
                    "type": "number",
                    "description": "Chamfer distance in METERS. E.g., 2mm = 0.002.",
                },
                "operation": {
                    "type": "string",
                    "enum": ["NEW", "ADD", "REMOVE"],
                    "description": "Reserved for API symmetry — chamfer always modifies the existing body.",
                    "default": "NEW",
                },
            },
            "required": ["did", "wid", "eid", "feature_id", "distance"],
        },
    ),
    Tool(
        name="export_stl",
        description=(
            "Export a Part Studio as an STL file for 3D printing or use in other CAD tools. "
            "STL files are saved locally on your machine.\n\n"
            "Units: 'millimeter' (default), 'centimeter', 'meter', 'inch', 'foot'."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "did": {"type": "string", "description": "Document ID"},
                "wid": {"type": "string", "description": "Workspace ID"},
                "eid": {"type": "string", "description": "Element ID (Part Studio)"},
                "output_path": {
                    "type": "string",
                    "description": "Full path to save the STL file (e.g., '/Users/name/Desktop/part.stl')",
                },
                "units": {
                    "type": "string",
                    "enum": ["millimeter", "centimeter", "meter", "inch", "foot"],
                    "description": "Export units",
                    "default": "millimeter",
                },
            },
            "required": ["did", "wid", "eid", "output_path"],
        },
    ),
    Tool(
        name="get_thumbnail",
        description=(
            "Get a shaded 3D view image of a Part Studio. "
            "This lets you visually 'see' the model — useful for verifying geometry, "
            "checking alignments, and debugging. Returns a PNG image.\n\n"
            "The image is saved to the specified path and also returned for display."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "did": {"type": "string", "description": "Document ID"},
                "wid": {"type": "string", "description": "Workspace ID"},
                "eid": {"type": "string", "description": "Element ID (Part Studio)"},
                "output_path": {
                    "type": "string",
                    "description": "Full path for the PNG image (e.g., '/tmp/part_preview.png')",
                },
                "width": {
                    "type": "integer",
                    "description": "Image width in pixels (max 1024, default 600)",
                    "default": 600,
                },
                "height": {
                    "type": "integer",
                    "description": "Image height in pixels (max 1024, default 400)",
                    "default": 400,
                },
            },
            "required": ["did", "wid", "eid", "output_path"],
        },
    ),
    Tool(
        name="onshape_help",
        description=(
            "Get help and conversion tables for working with Onshape. "
            "Includes unit conversions (mm→m), plane explanations, "
            "rate limit guidelines, and operation tips. "
            "Call this when you need a quick reference."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "topic": {
                    "type": "string",
                    "enum": ["units", "planes", "operations", "rate_limits", "pitfalls"],
                    "description": "Help topic",
                },
            },
            "required": ["topic"],
        },
    ),
]


# ── Help Text ───────────────────────────────────────────────────

HELP = {
    "units": (
        "Onshape uses METERS internally. All distances must be converted:\n"
        "  1 mm   = 0.001 m\n"
        "  1 cm   = 0.01 m\n"
        "  1 inch = 0.0254 m\n\n"
        "Quick conversions:\n"
        "  5mm = 0.005  |  10mm = 0.01  |  50mm = 0.05  |  100mm = 0.1\n"
        "  Ø60.5mm → radius = 0.03025  |  Ø210mm → radius = 0.105\n\n"
        "💡 Common mistake: Sending '5' instead of '0.005' for 5mm → "
        "results in a 5-meter part!"
    ),
    "planes": (
        "Standard planes in Onshape:\n"
        "  TOP   = XY plane (default, horizontal)\n"
        "  FRONT = XZ plane (vertical, facing you)\n"
        "  RIGHT = YZ plane (vertical, side)\n\n"
        "Offset: Positive offset moves the plane AWAY from origin.\n"
        "  TOP plane at offset=0.05 → Z=50mm above origin\n"
        "  FRONT plane at offset=-0.01 → X=-10mm behind origin\n\n"
        "For sketches on existing faces (e.g., disc face for bolt holes),\n"
        "calculate the offset from the part's position."
    ),
    "operations": (
        "Sketching workflow:\n"
        "  1. create_sketch → pick a plane\n"
        "  2. add_circle/add_line/add_rectangle → draw geometry\n"
        "  3. extrude or revolve → turn into 3D\n\n"
        "Extrude operations:\n"
        "  NEW   → Creates a new separate body\n"
        "  ADD   → Merges with an existing body (needs merge_with_part_id)\n"
        "  REMOVE → Cuts material (uses all existing bodies as targets)\n\n"
        "Revolve operations (v0.2):\n"
        "  Revolves a sketch profile around an axis. Default is Z-axis (0,0,0)→(0,0,1).\n"
        "  Used for round parts: cylinders, wheels, tires, shafts.\n"
        "  ⚠️ Profile must be closed and NOT cross the revolve axis.\n\n"
        "Deletion order: Children before parents.\n"
        "  Delete extrudes/revolves BEFORE deleting their parent sketches.\n"
        "  Delete features in reverse creation order."
    ),
    "rate_limits": (
        "Onshape rate limits aggressively at the ACCOUNT level.\n"
        "Safe: ~10 API calls per minute with 1+ second spacing.\n\n"
        "This MCP server handles rate limiting automatically:\n"
        "  - Sliding window: max 10 calls/60 seconds\n"
        "  - Minimum 1s between calls\n"
        "  - Exponential backoff on 429 (5s → 10s → 20s...)\n\n"
        "If operations are slow: the rate limiter is working. Be patient.\n"
        "If you get repeated 429 errors: wait 2-3 minutes before retrying.\n\n"
        "TIP: Each add_circle/add_line call = 1 API request.\n"
        "      A 15-line polygon = 15 calls → ~15 seconds minimum.\n"
        "      Prefer circles/rectangles over multi-line polygons when possible."
    ),
    "pitfalls": (
        "Common pitfalls when using Onshape via API:\n\n"
        "1. UNITS: Forgetting to convert mm→m. 5mm is 0.005, not 5.\n"
        "2. FEATURE ORDER: Deleting a sketch before its extrude corrupts the tree.\n"
        "3. REVOLVE: Supported via FeatureScript. Profile must NOT cross the revolve axis.\n"
        "4. EXTRUDE CUT: May fail on complex geometry. Fall back to manual UI.\n"
        "5. CLOSED POLYGONS: Sketches must form closed regions for extrusion.\n"
        "6. RATE LIMITS: Account-wide. Switching API keys doesn't give more quota.\n"
        "7. PART STUDIO CORRUPTION: A bad raw REST POST can corrupt a Part Studio.\n"
        "   Recovery requires a fresh document. Let the MCP server handle feature creation.\n\n"
        "See the full onshape skill for detailed workarounds and recipes."
    ),
}


# ── Tool Handler ────────────────────────────────────────────────

async def handle_call_tool(name: str, arguments: dict) -> list[TextContent]:
    """Route tool calls to the appropriate method."""
    client = get_client()

    try:
        if name == "onshape_help":
            topic = arguments.get("topic", "units")
            text = HELP.get(topic, HELP["units"])
            return [TextContent(type="text", text=text)]

        elif name == "list_documents":
            docs = client.list_documents(
                query=arguments.get("query", ""),
                limit=arguments.get("limit", 20),
            )
            return [TextContent(type="text", text=json.dumps(docs, indent=2))]

        elif name == "create_document":
            doc = client.create_document(name=arguments["name"])
            return [TextContent(type="text", text=json.dumps(doc, indent=2))]

        elif name == "get_document_info":
            info = client.get_document_info(did=arguments["did"])
            return [TextContent(type="text", text=json.dumps(info, indent=2))]

        elif name == "list_parts":
            parts = client.list_parts(
                did=arguments["did"],
                wid=arguments["wid"],
                eid=arguments["eid"],
            )
            return [TextContent(type="text", text=json.dumps(parts, indent=2))]

        elif name == "list_features":
            features = client.list_features(
                did=arguments["did"],
                wid=arguments["wid"],
                eid=arguments["eid"],
            )
            return [TextContent(type="text", text=json.dumps(features, indent=2))]

        elif name == "get_feature_info":
            feat = client.get_feature(
                did=arguments["did"],
                wid=arguments["wid"],
                eid=arguments["eid"],
                feature_id=arguments["feature_id"],
            )
            return [TextContent(type="text", text=json.dumps(feat, indent=2))]

        elif name == "delete_feature":
            result = client.delete_feature(
                did=arguments["did"],
                wid=arguments["wid"],
                eid=arguments["eid"],
                feature_id=arguments["feature_id"],
            )
            return [TextContent(type="text", text=json.dumps(result, indent=2))]

        elif name == "create_sketch":
            result = client.create_sketch(
                did=arguments["did"],
                wid=arguments["wid"],
                eid=arguments["eid"],
                name=arguments["name"],
                plane=arguments.get("plane", "TOP"),
                offset=arguments.get("offset", 0.0),
            )
            return [TextContent(type="text", text=json.dumps(result, indent=2))]

        elif name == "add_circle":
            result = client.add_circle(
                did=arguments["did"],
                wid=arguments["wid"],
                eid=arguments["eid"],
                sketch_id=arguments["sketch_id"],
                center_x=arguments["center_x"],
                center_y=arguments["center_y"],
                radius=arguments["radius"],
            )
            return [TextContent(type="text", text=json.dumps(result, indent=2))]

        elif name == "add_line":
            result = client.add_line(
                did=arguments["did"],
                wid=arguments["wid"],
                eid=arguments["eid"],
                sketch_id=arguments["sketch_id"],
                start_x=arguments["start_x"],
                start_y=arguments["start_y"],
                end_x=arguments["end_x"],
                end_y=arguments["end_y"],
            )
            return [TextContent(type="text", text=json.dumps(result, indent=2))]

        elif name == "add_rectangle":
            result = client.add_rectangle(
                did=arguments["did"],
                wid=arguments["wid"],
                eid=arguments["eid"],
                sketch_id=arguments["sketch_id"],
                corner1_x=arguments["corner1_x"],
                corner1_y=arguments["corner1_y"],
                corner2_x=arguments["corner2_x"],
                corner2_y=arguments["corner2_y"],
            )
            return [TextContent(type="text", text=json.dumps(result, indent=2))]

        elif name == "extrude":
            result = client.extrude(
                did=arguments["did"],
                wid=arguments["wid"],
                eid=arguments["eid"],
                sketch_id=arguments["sketch_id"],
                distance=arguments["distance"],
                operation=arguments.get("operation", "NEW"),
                merge_with_part=arguments.get("merge_with_part_id"),
            )
            return [TextContent(type="text", text=json.dumps(result, indent=2))]

        elif name == "revolve":
            result = client.revolve(
                did=arguments["did"],
                wid=arguments["wid"],
                eid=arguments["eid"],
                sketch_id=arguments["sketch_id"],
                axis_point=(
                    arguments.get("axis_point_x", 0),
                    arguments.get("axis_point_y", 0),
                    arguments.get("axis_point_z", 0),
                ),
                axis_direction=(
                    arguments.get("axis_dir_x", 0),
                    arguments.get("axis_dir_y", 0),
                    arguments.get("axis_dir_z", 1),
                ),
                angle_deg=arguments.get("angle_deg", 360),
                operation=arguments.get("operation", "NEW"),
            )
            return [TextContent(type="text", text=json.dumps(result, indent=2))]

        elif name == "fillet":
            result = client.fillet(
                did=arguments["did"],
                wid=arguments["wid"],
                eid=arguments["eid"],
                edge_feature_id=arguments["feature_id"],
                radius=arguments["radius"],
                operation=arguments.get("operation", "NEW"),
            )
            return [TextContent(type="text", text=json.dumps(result, indent=2))]

        elif name == "chamfer":
            result = client.chamfer(
                did=arguments["did"],
                wid=arguments["wid"],
                eid=arguments["eid"],
                edge_feature_id=arguments["feature_id"],
                distance=arguments["distance"],
                operation=arguments.get("operation", "NEW"),
            )
            return [TextContent(type="text", text=json.dumps(result, indent=2))]

        elif name == "export_stl":
            result = client.export_stl(
                did=arguments["did"],
                wid=arguments["wid"],
                eid=arguments["eid"],
                output_path=arguments["output_path"],
                units=arguments.get("units", "millimeter"),
            )
            return [TextContent(type="text", text=json.dumps(result, indent=2))]

        elif name == "get_thumbnail":
            result = client.get_thumbnail(
                did=arguments["did"],
                wid=arguments["wid"],
                eid=arguments["eid"],
                output_path=arguments["output_path"],
                width=arguments.get("width", 600),
                height=arguments.get("height", 400),
            )
            # Return both text and image path
            text = json.dumps(result, indent=2)
            path = result["path"]
            return [TextContent(type="text", text=f"{text}\n\nMEDIA:{path}")]

        else:
            return [TextContent(type="text", text=f"Unknown tool: {name}")]

    except Exception as e:
        logger.exception(f"Tool {name} failed")
        return [TextContent(type="text", text=f"Error: {e}")]


# ── Server Setup ────────────────────────────────────────────────

app = Server("onshape-mcp")


@app.list_tools()
async def list_tools() -> list[Tool]:
    return TOOLS


@app.call_tool()
async def call_tool(name: str, arguments: dict) -> list[TextContent]:
    return await handle_call_tool(name, arguments)


def main():
    """Entry point for the MCP server."""
    global _client
    logger.info("Starting Onshape MCP server")

    # Verify auth works at startup
    try:
        client = get_client()
        # Quick auth check — list one document
        client.list_documents(limit=1)
        logger.info("Onshape auth verified successfully")
    except Exception as e:
        logger.error(f"Onshape auth failed: {e}")
        print(f"⚠️  Onshape auth failed: {e}", file=sys.stderr)
        print("   Check ONSHAPE_DEV_ACCESS/ONSHAPE_DEV_SECRET env vars", file=sys.stderr)
        # Reset client so future get_client() calls will retry auth
        _client = None

    async def _run():
        async with stdio_server() as (read_stream, write_stream):
            await app.run(
                read_stream,
                write_stream,
                app.create_initialization_options(),
            )

    asyncio.run(_run())


if __name__ == "__main__":
    main()
