# Scorm-2-HTML
Converts Articulate Rise SCORM packages and converts them into a single HTML build document. Each block is identified to enable the ability to rebuild the course.

# Rise SCORM → single HTML content inventory

A **local, read-only, dependency-free** Python prototype for extracting course content from **Articulate Rise 360 SCORM ZIP exports** and presenting it in **one HTML document**. No LMS, API key, internet connection, or cloud upload is required.

## What it produces

- One HTML file with a table for every lesson, in course order.
- Left column: the source block's `family`, `type`, and `variant` where these are present.
- Right column: headings, text, lists, tables, tabs, accordions, media references, knowledge checks, quizzes, feedback and nested interactions, expanded into readable content.
- Images from the ZIP embedded into the HTML as Base64 (subject to size caps).
- Video/audio URLs displayed as links. Packaged video/audio paths are identified, and can optionally be embedded for small files.
- A collapsible **View original block data** section beneath each block to preserve inspectable source JSON and help audit unsupported block types.
- The output HTML does not require JavaScript or a web server.

## Requirements

Python 3.10 or later. Uses only the Python standard library. Tested with Python 3.11 on synthetic test packages. Currently tested working with kitchen sink data in a SCORM 1.2 ZIP package. Future testing required.

## Windows: file-picker workflow

1. Install Python from <https://www.python.org/downloads/> if necessary. During installation, enable **Add Python to PATH**.
2. Extract this project ZIP into a folder on your computer.
3. Double-click `Launch Rise Converter.bat`.
4. Choose your *original* Rise LMS/SCORM ZIP, without unzipping it.
5. Choose where to save the HTML file, then open the resulting HTML in a browser.

Alternatively, run `python rise_scorm_to_html.py` to open file-picker dialogs.

## Command-line workflow

```powershell
py -3 rise_scorm_to_html.py "C:\Courses\my-rise-export.zip"
```

Save to a specific file:

```powershell
py -3 rise_scorm_to_html.py "C:\Courses\my-rise-export.zip" -o "C:\Courses\course-content.html"
```

Optional switches:

- `--embed-video`: embed eligible locally packaged video/audio files (each limited to 32 MB; 200 MB combined media limit). **This can make the final HTML very large.**
- `--no-raw`: exclude the collapsible raw JSON view to reduce output size.
- `-h`: show help.

## Export formats covered

The tool attempts to decode Base64-encoded JSON stored in:

- `scormcontent/runtime-data.js` (e.g. `__jsonp("runtime-data.js", "...")`)
- Rise `und.js` bundles (e.g. `__resolveJsonp("course:und", "...")`)
- Earlier `scormcontent/index.html` / `window.courseData` / `deserialize(...)` patterns
- Direct course JSON files, if present

It deliberately does **not** execute arbitrary JavaScript inside an uploaded package.

## Important limits

This is an **initial prototype**. Rise's export internals are not a stable, officially documented content-extraction API and may change. Some older LZW/LZString-compressed exports are not yet handled. Support for newly introduced blocks may require mapping additional fields after seeing a real example.

It is a **static audit/documentation export**, not a live recreation of Rise. Quizzes will not be scored; Storyline or custom-code interactions cannot be converted into fully functioning interactions by simply reading their parent block. Videos that exist only as packaged files are *referenced* by name by default, not playable in the stand-alone HTML. Images larger than 16 MB each, or beyond the 200 MB total budget, are not embedded. Remote assets need internet access to display.

Review the finished HTML against the original course, particularly custom blocks, interactive scenarios, graded assessments and URLs. The original block data is available under each table row to investigate any extraction gaps.

## Safety and data-handling

The tool does not send data anywhere. It reads the ZIP in memory and writes an HTML report to a location you choose. It does not overwrite or modify the SCORM export. It removes scripts, iframe elements, embedded object elements, event-handler attributes and unsafe hyperlinks from rich text in the HTML report. Avoid sharing reports that contain confidential institutional content or assessment answers.

## Testing

```powershell
py -3 -m unittest -v test_converter.py
```

These tests use fabricated course data and test three encoding wrappers and HTML sanitisation.

## Licence

MIT. This independent tool is not affiliated with Articulate. Articulate and Rise are trademarks of their respective owners.
