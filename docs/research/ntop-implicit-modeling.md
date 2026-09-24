# nTop (nTopology) — implicit modeling og hvordan vi bruger det med Onshape

> Research note til onshape-mcp. Skrevet 23/9-2026 af Cille på Michaels anmodning
> ("Undersøg hvad ntop er i cad og hvordan kan vi bruge det når vi designer i cad til onshape").
> Kilder er søgt og verificeret via web research (ntop.com, 3mf.io, Siemens AM blog, Onshape docs).

## Hvad er nTop?

nTop (tidligere nTopology Element, i dag "nTop Platform"/"nTop Core") er et **computational design**-værktøj
bygget omkring **implicit modeling** — en fundamentalt anden måde at repræsentere geometri på end
B-rep (Onshape, SolidWorks, NX) eller meshes (STL):

| Repræsentation | Geometri defineres som | Robusthed | Typisk brug |
|---|---|---|---|
| **B-rep** | netværk af kanter/flader (NURBS) | skør — filéts/kernel-fejl | præcisions-CAD, drawings |
| **Mesh** | trekanter/punkter | grov, diskretiseret | visualisering, print |
| **Implicit (SDF)** | **matematisk felt** f(x,y,z) = 0 — signed distance function | 100% robust — altid evaluerbar | latricer, TPMS, organisk geometri |

Kernen: en solid er en funktion der returnerer **afstanden til overfladen** (negativ indeni,
positiv udenfor, nul på overfladen). Fordi geometrien er en ligning — ikke en liste af flader —
kan den ændres, kombineres og evaluationsstyres **uden topologiske fejl**. En gyroid er en
ligning, ikke 100.000 flader.

## De fire begreber du skal kende

1. **Implicit body**: et objekt repræsenteret som SDF. Konverteres altid lossless til
   mesh/quad-mesh ved ønsket opløsning.
2. **Field (felt-drevet design)**: et rumligt varierende input (scalar field) som styrer
   geometri parametrisk — fx wall thickness, strut diameter, lattice density pr. punkt.
   Felter kan komme fra FEA-resultater (spændinger), CAD-geometri (afstandsfelter),
   eksperimenterede data eller rene funktioner. Dette er nTops kendetegn:
   **geometri styres af ingeniørdata, ikke af manuel modellering**.
3. **Lattice / TPMS**: gentagne cellestrukturer (struts/beams) eller tripelt-periodiske
   minimalflader (Gyroid, Schwarz P/Diamond). I implicit form er de analytiske udtryk —
   eksakte ved al opløsning, og de kan graderes kontinuert (solid i midten, tynd i
   kanterne) via felter.
4. **Homogenisering**: udledning af bulk-materialeegenskaber (E-modul, varmeledning) fra
   en celles geometri — bruges til at vælge celle/topologi ud fra ønskede egenskaber,
   før man genererer geometrien.

## Hvorfor det er relevant for os (Onshape-arbejds流程)

Onshape er B-rep og kommer ALDRI til at lave gyroider og graduerede latricer nativt —
det er ikke hvad B-rep-kerner er bygget til (lignende: NX har et implicit-modul, men det
er en separat enhed netop af den grund). Pointen er ikke at erstatte Onshape, men at
kende **når en opgave hører hjemme i implicit-domænet** og vide hvordan de to taler sammen.

**To integration-veje:**

### A) Onshape → nTop (design space)
1. Design den ydre form (interface-flader, mounts, passager) i Onshape — parametrisk, med
   drawings og assemblies som normalt.
2. Eksportér STEP (eller STL for simple spaces) af design space / body.
3. Importér i nTop → konvertér til implicit body (genererer automatisk afstandsfelter).
4. Fyld med lattice/TPMS, styret af felter (fx tykkelse som funktion af afstand til
   belastede interface-flader).
5. Eksportér resultatet og tag det TILBAGE til Onshape som reference/mesh for
   dokumentation og assembly-kontrol.

### B) nTop → Onshape (færdig geometri)
Den pragmatiske returvej:
- **3MF med Beam Lattice extension** — nTop eksporterer mesh + beam-lattices i ÉN fil.
  Onshape importerer 3MF som mesh. NB: beam-lattice-delen behandles som mesh-geometri,
  ikke som B-rep. Fint til visualisering, volumefyld og print-prep; ikke til at filéte videre på.
- **STL/OBJ** til lukkede flader (gyroid-sheets konverteret til mesh) — samme status: mesh i Onshape.
- **STEP** hvis man mesh'er latricen op i høj opløsning og konverterer til B-rep
  (CAD Exchanger etc.) — tungt, kun når en leverandør kræver STEP, ellers undgås.

**Den professionelle pattern**: Onshape ejer *interface og drawings*, nTop ejer *indre
strukturen*, og 3MF er den bærende overførsel. Faktisk print sker direkte fra nTop-outputs
(Bambu Studio læser 3MF nativt, også med lattice — relevant for vores print-workflow).

## Hvad betyder det konkret for onshape-mcp?

1. **FeatureScript-genererede latricer i Onshape er fake**: kan lave simple beam-patterns
   via patterns/sketch-arrays, men det kollapser på reel cellestørrelse/gradering.
   Anbefal altid nTop (eller tilsvarende implicit-værktøj) når kravet er lattice/TPMS/graded.
2. **Ny værktøjs-kilde til MCP**: når en Hermes-bruger beder om "lightweight", "lattice",
   "gyroid infill", "topology-optimeret struktur" i Onshape-sammenhæng, skal svaret
   indeholde grænsedragningen — og evt. en pipeline hvor onshape-mcp eksporterer
   design space STEP og peger videre.
3. **Eksport fra onshape-mcp**: vi har allerede STEP/STL-export. **3MF-export mangler**
   og ville gøre os til den naturlige bro til print (Bambu) og nTop (lattice-3MF læses
   retur som beams i mange AM-tools). Fremtidig feature, ikke kritisk.
4. **Målfilformat-kendskab**: 3MF Beam Lattice extension (matizero-TPMS-repo i ref listen
   dokumenterer invarianten "boundary shell" som nTop også håndhæver — solid skal altid
   have lukket skal selvom latricen er åben).

## Kilder (verificeret 23/9-2026)

- ntop.com — Implicit modeling for mechanical design; Implicits and fields for beginners
- learn.ntop.com — Intro to field-driven design (fields fra CAD/mesh ved implicit-konvertering)
- 3mf.io — "How to Export a Mesh & Lattice 3MF from nTopology" (beam-extension, kun beam-baserede
  lattices kan eksporteres som 3MF-beams; TPMS-sheets må mesh'es)
- blogs.sw.siemens.com/additive — NX implicit-modulet (TPMS-præsets, equation builder) — viser
  at også de store CAD-huse løser det som separat modul
- support.ntop.com — Gyroid via Periodic Lattice/Gyroid Body blocks
- zanerobotics.substack.com — god teknisk gennemgang af SDF vs B-rep vs mesh
- github.com/matizero1/tpms-generative-lattice — C99-reference til TPMS-generering

## Bundet til vores virkelighed

- Propel-CAD (Muslingevagten): latricer kunne reducere masse i blade — men print i metal/
  komposit kræver lukket skal; nTop ville give det, men er overkill for nuværende blade.
- CRISO-robot: vægt-reduktion i krybberums-robotens struktur er en oplagt nTop-kandidat
  (lille serie, printet, belastningsfelter kendte).
- OrganicRing/juveler: SDF-tilgangen er identisk med hvad vi allerede gør i SceneKit
  (edge-deformation via felter) — samme matematik, andet værktøj.
