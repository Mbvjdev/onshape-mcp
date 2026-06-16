# onshape-mcp

MCP-server til Onshape CAD — semantiske værktøjer der wrapper Onshapes REST API med rate limiting, caching og auth-håndtering.

**Hvad:** 15+ værktøjer der oversætter "Lav en skitse med fire Ø12mm huller på en Ø175mm cirkel" → API-kald, uden at du skal tænke på btTypes, transient IDs, eller rate limits.

**Hvorfor:** Onshapes API er kraftfuld men rå — 17 API-kald for en boltskabelon, rate limits der trigger på 20+ kald i minuttet, og FeatureScript for at få fat i transient face IDs. Den her server abstraherer alt det væk bag semantiske værktøjer.

**Status:** v0.1 — sketches, extruder, parts, features, STL-eksport og thumbnails virker. Revolve kommer i v0.2.

## Installation

```bash
cd ~/dev/onshape-mcp
~/dev/onshape-venv/bin/pip install -e .
```

Afhængigheder (installeret i `~/dev/onshape-venv/`):
- `mcp` — MCP Python SDK (stdio server)
- `httpx` — HTTP client til REST-kald
- `cachetools` — TTL cache
- `onpy` — Onshape Python library (til feature creation)

## Auth

Serveren læser Onshape API-nøgler i denne rækkefølge:

1. `ONSHAPE_DEV_ACCESS` + `ONSHAPE_DEV_SECRET` env vars
2. `ONSHAPE_ACCESS_KEY` + `ONSHAPE_SECRET_KEY` env vars
3. `~/.onpy/config.json`

## Kørsel

### Manuel test
```bash
cd ~/dev/onshape-mcp
PYTHONPATH=src ~/dev/onshape-venv/bin/python -m onshape_mcp.server
```

### Via Hermes Agent
Serveren er konfigureret i `~/.hermes/config.yaml` under `mcp_servers.onshape`.
Hermes starter den automatisk ved næste genstart. Tools dukker op som `mcp_onshape_*`.

## Værktøjer

### Dokumenter
| Værktøj | Beskrivelse |
|---------|-------------|
| `list_documents` | Søg/list dokumenter. Returnerer navn, ID, ejer. |
| `create_document` | Opret nyt dokument. Returnerer doc ID + workspace ID. |
| `get_document_info` | Detaljer om dokument: workspaces, elements (Part Studios). |

### Parts
| Værktøj | Beskrivelse |
|---------|-------------|
| `list_parts` | List alle parts i et Part Studio: navn, type, materiale, masse. |
| `get_feature_info` | Detaljer om en specifik feature. |

### Features
| Værktøj | Beskrivelse |
|---------|-------------|
| `list_features` | List alle features i et Part Studio med typer og suppression status. |
| `delete_feature` | Slet en feature (⚠️ children før parents). |

### Skitser
| Værktøj | Beskrivelse |
|---------|-------------|
| `create_sketch` | Opret skitse på TOP/FRONT/RIGHT plan, evt. med offset. |
| `add_circle` | Tilføj cirkel: center (x,y) + radius. Alle mål i METER. |
| `add_line` | Tilføj linje: start → slut punkt. |
| `add_rectangle` | Tilføj rektangel: to modstående hjørner. |

### 3D
| Værktøj | Beskrivelse |
|---------|-------------|
| `extrude` | Extrudér skitse → 3D body. Operation: NEW, ADD, REMOVE. |

### Eksport
| Værktøj | Beskrivelse |
|---------|-------------|
| `export_stl` | Eksportér Part Studio som STL (mm/cm/m/inch/foot). |
| `get_thumbnail` | Hent shaded 3D-view som PNG — "se" modellen. |

### Hjælp
| Værktøj | Beskrivelse |
|---------|-------------|
| `onshape_help` | Hurtig reference: units, planes, operations, rate limits, pitfalls. |

## Enheder

**ALT er i METER.** Dette er Onshapes native enhed.

```
1 mm = 0.001 m
1 cm = 0.01 m
1 m  = 1.0 m
```

Eksempler:
- Ø10mm hul → `radius=0.005`
- 50mm offset fra TOP → `offset=0.05`
- 76mm extrude → `distance=0.076`
- Ø175mm cirkel → `radius=0.0875`

## Rate Limiting

Serveren håndterer rate limits automatisk:
- **Sliding window:** Max 10 kald pr. 60 sekunder
- **Minimum interval:** 1 sekund mellem kald
- **Eksponentiel backoff:** Ved 429 starter backoff på 5s → 10s → 20s → ... max 120s
- **Cache:** GET-responses caches i 30-120 sek (afhængigt af type)

Hvis kald tager lang tid: rate limiteren arbejder. Vær tålmodig.

## Typiske workflows

### Opret et nyt emne med huller
```
1. create_document("Mit Emne")          → få did, wid, eid
2. create_sketch(did, wid, eid, "Huller", plane="TOP")
3. add_circle(..., center_x=0, center_y=0, radius=0.044)  # Ø88mm centerhul
4. add_circle(..., center_x=0.05, center_y=0, radius=0.004)  # Ø8mm bolthul
5. extrude(did, wid, eid, sketch_id, distance=0.01, operation="NEW")
6. get_thumbnail(...)                    → se resultatet
```

### Inspektér en eksisterende Part Studio
```
1. list_documents("mit projekt")         → find document ID
2. get_document_info(did)                → find workspace + element IDs
3. list_features(did, wid, eid)          → se feature-træet
4. list_parts(did, wid, eid)             → se alle parts
```

### Byg videre på en eksisterende part
```
1. list_parts(did, wid, eid)            → find part_id
2. create_sketch(did, wid, eid, "Nyt hul", plane="TOP", offset=0.01)
3. add_circle(..., radius=0.005)
4. extrude(did, wid, eid, sketch_id, distance=0.01, operation="REMOVE")
```

## Forskelle fra rå API

| Rå API | onshape-mcp |
|--------|-------------|
| Manuelle btType-strings | Vælg "TOP"/"FRONT"/"RIGHT" |
| Transient ID-helvede | Sker automatisk ved extrude-remove |
| 17 kald for boltskabelon | `create_sketch` + 5× `add_circle` |
| FeatureScript for revolve | (kommer i v0.2) |
| Manuelle enhedskonverteringer | Indbygget mm→m |
| Rate limit 429 → crash | Automatisk backoff + retry |
| Cache selv | Indbygget TTL cache |

## Kendte begrænsninger

- **Revolve:** Ikke supporteret endnu (v0.2). Brug ring-extrude pattern (to koncentriske cirkler).
- **Extrude REMOVE:** Bruger REST + FeatureScript. Kan fejle på kompleks geometri.
- **Sketch polygoner:** Hver `add_line` = 1 API-kald. 15+ linjer trigger rate limits.
- **Part Studio korruption:** En forkert formateret REST POST kan korrumpere en Part Studio. Serveren håndterer feature creation via onpy for at undgå dette.
- **Sletning af dokumenter:** Onshape tillader ikke permanent sletning via API (403). Brug UI.

## Udvikling

```bash
cd ~/dev/onshape-mcp
source ~/dev/onshape-venv/bin/activate

# Test import
python -c "from onshape_mcp.client import OnshapeClient; print('OK')"

# Kør MCP server manuelt (til debugging)
python -m onshape_mcp.server
```

Projektstruktur:
```
onshape-mcp/
├── pyproject.toml
├── README.md                     ← du er her
└── src/onshape_mcp/
    ├── __init__.py
    ├── server.py                 ← MCP server (stdio)
    ├── client.py                 ← OnshapeClient (REST + onpy wrapper)
    ├── rate_limiter.py           ← Sliding window + backoff
    └── cache.py                  ← TTL cache
```
