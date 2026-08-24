# Landmark Forge

Massives Blender-4.x-Python-Skript, das vier ikonische Bauwerke als **prozedurale 3D-Modelle** erzeugt:

1. **Hogwarts Castle** — Fan-Interpretation einer gotischen Schloss-Schule (nicht mit Warner Bros. oder J. K. Rowling verbunden)
2. **ETH Zürich** — Hauptgebäude nach Gottfried Semper, Kuppel von Gustav Gull
3. **MI6 / SIS Building** — Vauxhall Cross, Terry Farrell 1994
4. **Sydney Opera House** — Jørn Utzons sphärische Schalen auf Bennelong Point

Das gesamte Generator-Skript steckt in **einer Datei**: [`landmark_forge.py`](./landmark_forge.py) (~3 000 Zeilen, keine Extra-Addons).

Repository: [github.com/lolalpha00gamma/blender-landmarks](https://github.com/lolalpha00gamma/blender-landmarks)

---

## In Blender ausführen

1. Blender **4.0+** öffnen (4.2 / 4.5 empfohlen).
2. Workspace **Scripting**.
3. `Open` → `landmark_forge.py`.
4. **Run Script**.

In der Sidebar des 3D-Viewports (`N`) erscheint das Panel **Landmark Forge**: einzelne Gebäude an/aus, Tag/Nacht, Fenstendichte, Seed, GLB-Export.

### Kommandozeile

```bash
blender --background --python landmark_forge.py
blender --background --python landmark_forge.py -- --no-hogwarts --day --export
```

Argumente nach `--`:

| Flag | Wirkung |
| --- | --- |
| `--no-hogwarts` `--no-eth` `--no-mi6` `--no-sydney` | Gebäude weglassen |
| `--day` / `--night` | Beleuchtung |
| `--no-museum` | Keine gemeinsame Plaza |
| `--export` | GLB nach `//exports/` |
| `--seed=42` | Zufalls-Seed für Bäume/Felsen |

---

## Was das Skript baut

### Hogwarts (Fan-Architektur)
Klippe, Schwarzer See, Steinviadukt, Große Halle mit Spitzbogenfenstern und Strebepfeilern, Uhrenturm mit Zifferblättern, Kreuzgang, Keep, Astronomie-Turm, runde Kegeldachtürme, Gewächshäuser, Holzbrücke, Bootshaus, Kiefern.

### ETH Zürich
Sandsteinflügel um zwei Höfe, rustiziertes Erdgeschoss, regelmäßiger Fensterhythmus, dunkles Ziegeldach, Polyterrasse mit Freitreppe, Säulentrommel und dunkle Kuppel mit Laterne.

### MI6 London
Zikkurat aus cremefarbenem Stein, grüne Glasblöcke mit Sprossen, gestufte Terrassen, Ädikula zur Themse, zylindrische Trommeln, Flussmauer, Platanen, Brückenstummel.

### Sydney Opera House
Granit-Podium, monumentale Südtreppe, Concert-Hall- und Opera-Theatre-Schalen als Kugelabschnitte, Restaurant-Schalen, Glasfoyers, Hafenwasser.

Jedes Gebäude landet in einer eigenen Collection (`LF_Hogwarts`, `LF_ETH_Zurich`, `LF_MI6_Vauxhall`, `LF_Sydney_Opera`) unter `LF_Landmarks`. Kameras und ein 240-Frame-Turntable sind dabei.

---

## Hinweise

- Hogwarts ist eine **eigenständige Fan-Interpretation** der öffentlich bekannten Massenverteilung einer schottischen Schloss-Schule. Kein Studio-Mesh, keine Marken-Assets.
- ETH, MI6 und Oper sind **architektonische Annäherungen** (Massing, Material, Silhouette), keine Vermessungsmodelle.
- Materialien nutzen Principled BSDF, kompatibel mit Blender 4.0 (Specular IOR Level / Transmission Weight) und älteren Socket-Namen.

MIT-Lizenz. Siehe [`LICENSE`](./LICENSE).
