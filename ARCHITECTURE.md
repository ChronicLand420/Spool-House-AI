# SHAI Architecture

This document describes the current architecture of Spool House AI, also called SHAI. It reflects the alpha project as implemented today and is intended to help developers and Codex sessions make focused changes without breaking the CLI, GUI, or processing pipeline.

## 1. Project Purpose

SHAI automates image-to-product preparation for simple 3D printable outputs. It accepts PNG, JPG, and JPEG artwork, cleans or preserves transparency, extracts printable silhouettes and internal detail masks, writes editable SVG files, generates basic STL relief meshes, and saves review previews for each stage.

The current project is not a complete slicer, mesh repair tool, AI image generator, or marketplace uploader. Those are planned expansion areas. The current core value is a shared CLI and desktop GUI pipeline that turns source artwork into inspectable intermediate assets and simple printable prototypes.

## 2. Folder Structure

```text
config/
  config.yaml              Default paths, pipeline settings, geometry settings, STL settings, preview settings.
docs/
  devlog/                  Human-readable development session notes and patch notes.
input/
  .gitkeep                 Watched and test input folder. Generated inputs are ignored by Git.
logs/
  .gitkeep                 Runtime log folder. Logs are ignored by Git.
output/
  .gitkeep                 Generated job output folder. Generated outputs are ignored by Git.
scripts/
  new_devlog.py            Creates dated, auto-incremented devlog entries.
spool_house_ai/
  __init__.py              Package metadata.
  config.py                Typed configuration loading.
  gui.py                   PySide6 desktop app.
  logging_setup.py         Console and rotating-file logger setup.
  main.py                  CLI entrypoint.
  pipeline.py              Shared image processing pipeline.
  test_mode.py             Built-in verification images and checks.
  watcher.py               Watchdog-based input folder watcher.
  processing/
    background.py          Background removal and cleaned PNG creation.
    analysis.py            Mask generation, feature classification, and image analysis.
    geometry.py            Contour cleanup, smoothing safety, SVG path helpers, geometry reports.
    preview.py             Stage previews, comparison images, and geometry debug previews.
    silhouette.py          Legacy/simple silhouette wrappers.
    stl.py                 Raster-mask relief mesh generation.
    vectorize.py           SVG output from vector contours.
.github/
  workflows/ci.yml         Windows CI for compile and pipeline test mode.
```

## 3. CLI Entrypoint

The CLI entrypoint is `spool_house_ai/main.py` and is launched with:

```powershell
python -m spool_house_ai.main
```

It parses these options:

- `--config`: YAML config path, defaulting to `config/config.yaml`.
- `--once`: Process all existing supported images in the input folder and exit.
- `--watch`: Intended watcher mode flag.
- `--product-mode`: Override `flat_relief`, `keychain`, or `wall_art`.
- `--threshold`: Override silhouette threshold value.
- `--height`: Override STL extrusion height.
- `--debug`: Enable debug logging.
- `--test`: Run built-in verification images and output checks.

`main()` loads config, applies CLI overrides, configures logging, imports runtime dependencies, builds an `ImagePipeline`, and then runs test mode, one-time processing, or the watcher. In the current implementation, if neither `--test` nor `--once` is used, the app starts watching the input folder.

## 4. GUI Entrypoint

The desktop GUI entrypoint is `spool_house_ai/gui.py` and is launched with:

```powershell
python -m spool_house_ai.gui
```

The GUI uses PySide6. `MainWindow` builds a three-panel workflow:

- Left panel: drag/drop queue, add image button, generate button, output open buttons, and review controls.
- Center panel: staged room cards for the pipeline rooms.
- Right panel: product, detail, threshold, smoothing, scale, keychain, and background-removal controls.

Processing runs in `PipelineWorker`, a `QThread` that creates its own logger and `ImagePipeline`. The worker passes stage callback updates back to the main UI thread through Qt signals. The GUI uses the same pipeline and config dataclasses as the CLI, so GUI settings are converted into a copied `AppConfig` before each job.

## 5. Configuration System

Configuration starts in `config/config.yaml` and is loaded by `spool_house_ai/config.py`.

`load_config()` resolves the config path, treats the config folder's parent as the project root, resolves relative input/output/log paths from that root, creates those folders if needed, and returns an `AppConfig` dataclass.

The config is split into frozen dataclasses:

- `PipelineConfig`: product mode, detail mode, background removal, debug flag.
- `WatcherConfig`: file stability polling.
- `SilhouetteConfig`: thresholding, smoothing, morphology, hole/detail preservation, contour cleanup, island removal, safety thresholds.
- `SvgConfig`: vectorizer backend and contour cleanup settings.
- `StlConfig`: scale, base height, relief height, detail heights, keychain options, bevel settings, mesh resolution.
- `PreviewConfig`: preview image size.

CLI and GUI changes do not mutate the existing config object directly. They use `dataclasses.replace()` to create updated dataclass copies.

## 6. Pipeline Flow

The shared pipeline lives in `spool_house_ai/pipeline.py` as `ImagePipeline.process()`.

For each input image, it:

1. Resolves the image path and creates `output/<image-stem>/`.
2. Defines output paths for cleaned PNG, silhouette PNG, masks, SVG, STL, preview, contour debug image, and job settings.
3. Emits the Intake Room stage update.
4. Runs `remove_background()` to create `<stem>_cleaned.png`.
5. Runs `analyze_image()` to create binary masks and vector contours.
6. Saves body, hole, and detail masks.
7. Runs `create_svg()` to write the editable SVG.
8. Runs `save_stage_previews()` to create review and geometry debug previews.
9. Runs `create_relief_stl()` to generate the STL.
10. Runs `create_preview()` to create the final simple raised preview.
11. Writes `job_settings.yaml`.
12. Emits Output Vault completion.

If STL generation fails, the pipeline keeps the cleaned PNG, masks, SVG, and previews when possible. The returned boolean indicates whether STL creation succeeded.

## 7. Stage Callback System

The pipeline exposes a lightweight callback type:

```python
StageCallback = Callable[[str, str, str, Path | None], None]
```

The fields are:

- `room`: Human-readable stage name, such as `Cleanup Lab` or `Mesh Forge`.
- `state`: `idle`, `active`, `done`, or `failed`.
- `message`: Short status text.
- `thumbnail`: Optional path to an image or output artifact.

`pipeline._emit()` calls the callback only when one is provided. CLI/test workflows usually pass no callback. The GUI passes a callback that emits `stage_changed` from `PipelineWorker`, and `MainWindow.update_room()` applies the update to the matching `RoomCard`.

The current stage list is:

- Intake Room
- Cleanup Lab
- Detail Analyzer
- Vector Workshop
- Mesh Forge
- Render Bay
- Output Vault

## 8. Processing Modules And Responsibilities

### background.py

`background.py` creates the cleaned PNG used by the rest of the pipeline.

- Loads input images with Pillow and converts to RGBA.
- Preserves already-transparent PNGs.
- Saves the original image when background removal is disabled.
- Checks for `rembg` and local model files before attempting AI background removal.
- Falls back to saving the source image when `rembg` is unavailable.

### analysis.py

`analysis.py` owns most image interpretation.

- Upscales RGBA input for cleaner mask processing.
- Builds alpha visibility masks and dark-detail masks.
- Supports adaptive thresholding.
- Removes small islands while optionally preserving islands near larger body regions.
- Smooths masks with median blur and morphology.
- Removes small features.
- Classifies body, holes, details, and major color regions.
- Chooses the final mask based on detail mode.
- Calls `extract_vector_contours()` and uses the vector mask when safety checks allow it.
- Saves the silhouette PNG and returns an `ImageAnalysis` object.

### geometry.py

`geometry.py` converts binary masks into cleaned vector contour data and diagnostic reports.

- Extracts raw OpenCV contours with hierarchy.
- Filters contours by area.
- Simplifies points with `approxPolyDP`.
- Merges nearly collinear points.
- Straightens long runs when safe.
- Fits simple curve sections conservatively.
- Preserves sharp corners.
- Rejects unsafe cleanup based on area, bounding box, aspect ratio, and point reduction.
- Provides fallback behavior and reports when fallback was used.
- Converts contours back to masks and SVG path strings.
- Writes geometry reports and debug overlays.

### vectorize.py

`vectorize.py` writes SVG files from vector contours.

- Accepts either `ImageAnalysis` or a raw mask.
- Reuses `analysis.vector_contours` when available.
- Falls back to extracting contours if needed.
- Detects requested `potrace` or `inkscape` availability, but currently writes through the internal OpenCV contour path flow.
- Writes an SVG with an `artwork` group and `edit-guides` paths for inspection in vector editors.

### stl.py

`stl.py` creates STL meshes from raster masks.

- Chooses the STL mask from vector contours, body mask, detail mask, or final mask depending on the analysis and detail mode.
- Applies product-specific mask preparation, including optional keychain loop and hole.
- Resizes large masks to stay under `max_mesh_pixels`.
- Builds per-pixel top heights for base, relief, raised details, engraving, and layered color relief.
- Creates a height-field mesh with top, bottom, and side faces.
- Exports through `trimesh`.

This is currently raster-derived relief generation, not true vector path extrusion.

### preview.py

`preview.py` generates human-reviewable images.

- Creates the final simple raised preview.
- Saves previews for original, cleaned, threshold, contours, body mask, hole mask, detail mask, SVG-style final mask, and STL-style shading.
- Saves side-by-side comparison images.
- Saves removed-island debug output.
- Saves V4/V5 geometry debug files such as raw contours, smoothed contours, final vector preview, before/after overlay, and `geometry_report.txt`.

## 9. Product Modes

Product modes are configured through `pipeline.product_mode` and `stl.product_mode`.

- `flat_relief`: General raised relief output for signs, emblems, plaques, and simple art.
- `keychain`: Slightly stronger relief behavior, with optional generated keychain loop and circular hole.
- `wall_art`: Thicker display-oriented relief using a taller product height multiplier.

Unsupported product modes raise an error in STL mask preparation.

## 10. Detail Modes

Detail modes control how holes and internal strokes affect output.

- `silhouette_only`: Uses the final silhouette without hole/detail classification.
- `preserve_holes`: Keeps true holes and negative spaces cut out of the body.
- `raised_details`: Keeps the body as a base and adds internal dark details as raised regions.
- `engraved_details`: Keeps the body as a base and lowers internal details into the top surface.
- `layered_color_relief`: Attempts stepped heights for major color regions and details.

`SilhouetteConfig.default_detail_behavior` can further alter whether details are treated as cut, ignored, or preserved.

## 11. Geometry Safety And Fallback System

Geometry cleanup is intentionally conservative. Every contour starts with original raw points, then moves through approximation, collinear cleanup, straightening, curve fitting, and smoothing.

Safety checks compare candidate geometry against the original contour:

- Area change percentage.
- Bounding box change percentage.
- Aspect ratio change percentage.
- Point reduction percentage.
- Minimum viable point count.

If a cleanup step fails safety checks, SHAI rejects that candidate and uses a safer earlier version. If the final smoothed contour is unsafe, SHAI falls back to a lower-tolerance approximation and marks fallback usage in `GeometryReport`.

`analyze_image()` only replaces the final mask with the vector-rendered mask when vector output exists and no global fallback was used. This helps prevent aggressive smoothing from silently changing the printable body.

The geometry report records:

- Original and smoothed contour counts.
- Original and smoothed point totals.
- Area, bounding box, aspect ratio, and point reduction changes.
- Whether fallback was used.
- Smoothing profile.
- Straightened and curve-fitted segment counts.
- Rejected cleanup count.

## 12. Output File Structure

For `example.png`, the pipeline writes to `output/example/`.

```text
output/example/
  example_cleaned.png
  example_silhouette.png
  example_body_mask.png
  example_hole_mask.png
  example_detail_mask.png
  example_contour_debug.png
  example.svg
  example.stl
  example_preview.png
  example_preview_original.png
  example_preview_cleaned.png
  example_preview_threshold.png
  example_preview_contours.png
  example_preview_body_mask.png
  example_preview_hole_mask.png
  example_preview_detail_mask.png
  example_preview_svg.png
  example_preview_stl.png
  raw_threshold.png
  raw_contours.png
  smoothed_contours.png
  final_vector_preview.png
  geometry_before_after_overlay.png
  geometry_report.txt
  removed_islands_debug.png
  original_vs_cleaned_compare.png
  original_vs_body_mask_compare.png
  original_vs_detail_mask_compare.png
  original_vs_final_vector_compare.png
  original_vs_stl_preview_compare.png
  job_settings.yaml
```

Generated files under `input/`, `output/`, and `logs/` are intentionally ignored by Git except for `.gitkeep` placeholders.

## 13. Test Mode

Test mode is launched with:

```powershell
python -m spool_house_ai.main --test
```

`spool_house_ai/test_mode.py` creates synthetic verification artwork in `input/`, processes each image through the full pipeline, and checks for required output files and geometry behavior.

Current test coverage includes:

- V2 artwork output verification.
- V4 geometry smoothing and contour point reduction.
- V4 real-world shape checks for bounding box and aspect ratio preservation.
- Aggressive smoothing fallback verification.
- V5 island removal and internal detail preservation.

Test mode returns failure when expected files are missing or geometry safety expectations are not met.

## 14. GitHub And CI Workflow

CI is defined in `.github/workflows/ci.yml`.

The workflow runs on:

- `push`
- `pull_request`

The job uses:

- `windows-latest`
- Python `3.12`
- `python -m pip install -r requirements.txt`
- `python -m compileall spool_house_ai`
- `python -m spool_house_ai.main --test`

The contributing workflow expects developers to:

- Work from a branch.
- Keep generated files out of commits.
- Create a devlog entry for meaningful development sessions.
- Keep changes focused.
- Update documentation when behavior changes.
- Use semantic version tags for releases, starting from `v0.1.0-alpha`.

## 15. Known Weaknesses

- STL generation is raster-mask height-field generation, not true vector extrusion.
- Generated meshes still need broader slicer validation with representative real artwork.
- Mesh repair and manifold validation are not implemented.
- GUI behavior is manually verified; there are no automated GUI tests.
- The `--watch` flag exists, but the CLI currently watches by default whenever `--test` and `--once` are not used.
- Background removal depends on optional local `rembg` model files and is disabled by default.
- External vectorizer backends are detected but not fully integrated as separate vectorization pipelines.
- Geometry tests are built into `--test`, but there are no focused unit tests around individual safety checks.
- Queue persistence, job history, packaging, and release automation are not implemented.
- Large or complex masks may produce large SVG/STL files and slower processing.

## 16. Future Roadmap

Near-term roadmap:

- Add true vector extrusion so SVG/path geometry can drive STL creation directly.
- Add mesh repair, manifold validation, and slicer-oriented quality checks.
- Add unit tests around contour safety checks, fallback behavior, island removal, and detail classification.
- Improve GUI review controls after geometry output stabilizes.
- Add job history and queue persistence.

Later roadmap:

- Add AI cleanup, upscale, and vectorization options after the deterministic pipeline is stable.
- Add slicer integration for tools such as Bambu Studio and related print workflows.
- Add marketplace packaging for Printables, MakerWorld, Creality, and similar destinations.
- Add automatic product descriptions and upload package generation.
- Add dashboard/database support for stored jobs, presets, and output history.
