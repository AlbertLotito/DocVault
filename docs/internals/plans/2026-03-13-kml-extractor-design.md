# KML/KMZ Extractor — Design Document
_2026-03-13_

## Goal

Index KML and KMZ geographic files by extracting placemark names and descriptions. Makes location data searchable via FTS and semantic search.

---

## Architecture

Single new kernel: `extractors/kml_extractor.py`

- Standard kernel pattern: `MANIFEST` dict, `extract(file_path, ctx) → (text, err, meta)` 3-tuple.
- Registered as `com.docvault.system.kml` — auto-certified on startup.
- **No new dependencies** — stdlib `xml.etree.ElementTree` + `zipfile` only.

**Extensions covered:** `.kml` `.kmz`

---

## KMZ Handling

KMZ is a ZIP archive containing a `.kml` file (typically `doc.kml`). The extractor:
1. Opens with `zipfile.ZipFile`
2. Finds the first entry with a `.kml` extension
3. Reads it as bytes in-memory (no temp files)
4. Parses as XML

---

## KML Namespace Detection

KML files use one of two XML namespaces:
- `http://www.opengis.net/kml/2.2` (modern)
- `http://earth.google.com/kml/2.1` (legacy Google Earth)

The extractor detects the namespace from the root element tag and uses it for all subsequent XPath queries.

---

## Data Extracted

- Placemark `<name>` — text content
- Placemark `<description>` — text content, HTML-stripped, CDATA-decoded

Placemarks with no name are skipped. Descriptions are stripped of all HTML tags (Google Earth embeds `<b>`, `<br>`, tables, CDATA blocks).

---

## Output Format

```
KML Document: My Road Trip
Placemarks: 14

Eiffel Tower
  Iconic iron lattice tower on the Champ de Mars in Paris, France.

Notre-Dame Cathedral
  Medieval Catholic cathedral on the Île de la Cité in Paris.

Golden Gate Bridge
  (no description)
```

---

## Error Handling

| Condition | Result |
|---|---|
| Malformed XML | `(None, "KML parse error: ...")` |
| KMZ with no .kml entry | `(None, "No KML file found in KMZ")` |
| Zero placemarks | Valid result: `"Placemarks: 0\n\n(no placemarks)"` |
| Missing file | `(None, "KML read failed: ...")` |

---

## What Is NOT in Scope

- Coordinates (lat/lon)
- Folder hierarchy
- Geometry type counts (Point/LineString/Polygon)
- Network links or ground overlays
- Embedded imagery in KMZ
