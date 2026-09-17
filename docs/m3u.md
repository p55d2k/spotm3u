# M3U Generation

## Purpose

Generate a local M3U playlist from successfully resolved local audio files.

The M3U writer should be independent of Flask and Exportify.

---

## Input

The writer receives ordered `ResolvedTrack` objects.

```python
ResolvedTrack(
    track=Track(...),
    local_path=Path(...)
)
```

---

## Output

The generated file should:

- use UTF-8
- begin with `#EXTM3U`
- contain appropriate `#EXTINF` entries
- reference valid local paths
- preserve the supplied track order

---

## Example Structure

Conceptually:

```text
#EXTM3U
#EXTINF:<duration>,<artist> - <title>
/path/to/song.mp3
```

The exact metadata formatting should be implemented consistently.

---

## Paths

Support configurable path output where useful:

- absolute paths
- relative paths

The chosen behavior should be explicit and documented.

Paths must be properly represented for the target operating system.

---

## Ordering

The writer must never sort tracks unless explicitly instructed.

The input order is authoritative.

---

## Duplicates

Do not deduplicate.

If the source playlist contains:

```text
A
B
A
```

the generated M3U should contain all three entries.

Any deduplication decision belongs upstream and only applies to confirmed parser artifacts.

---

## Validation

The writer should avoid generating references to nonexistent files where practical.

The resolver should normally ensure that only valid `ResolvedTrack` objects reach the writer.

---

## Independence

The M3U writer must not:

- access Spotify
- parse Exportify files
- scan the music library
- render HTML
- depend on Flask request state
