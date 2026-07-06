# Spool House AI

Spool House AI automates image-to-product preparation for simple 3D printable STL files. V3 adds a real PySide6 desktop app on top of the existing CLI, plus richer image analysis for body masks, true holes, internal detail strokes, and color-region relief experiments.

Created by ChronicLand420.

## V3 Workflow

1. Watch or scan the `input/` folder.
2. Detect new `.png`, `.jpg`, or `.jpeg` files.
3. Remove the background when `rembg` is available.
4. Build configurable threshold, body, hole, and detail masks.
5. Remove tiny noise while preserving meaningful holes and details.
6. Generate editable SVG paths with contour guide layers.
7. Generate STL product output in one of these modes:
   - `flat_relief`
   - `keychain`
   - `wall_art`
8. Save cleaned PNG, silhouette PNG, SVG, STL, final preview, and stage previews.

Future features such as AI image generation, Blender automation, mesh repair, dashboard, queueing, database, upload packages, and slicer integrations are intentionally not implemented yet.

## Desktop App

Launch the GUI:

```powershell
python -m spool_house_ai.gui
```

Create a Windows desktop shortcut for the GUI:

```powershell
python scripts/create_desktop_shortcut.py
```

This creates `Spool House AI GUI` on the current user's Desktop and points it at this repository with the repo root as the working directory.

The app supports:

- Drag/drop PNG or JPG files into the queue.
- Click `Add Image` to browse for files.
- Choose product and detail settings.
- Click `Generate Product`.
- Watch rooms light up as stages run: Intake Room, Cleanup Lab, Detail Analyzer, Vector Workshop, Mesh Forge, Render Bay, and Output Vault.
- Open the output folder, STL, SVG, or preview after generation.

The visual theme is an original underground maker bunker/factory interface. It does not use Fallout Shelter assets, names, characters, or copied art.

## Screenshots

Placeholder:

```text
docs/screenshots/gui-main.png
docs/screenshots/gui-processing.png
docs/screenshots/gui-output.png
```

## Development Log

Session patch notes live in [`docs/devlog/`](docs/devlog/). Each meaningful development session should create one entry that records what changed, why, how it was tested, and what still needs attention.

## Patch Notes Workflow

Before changes:

```powershell
python scripts/new_devlog.py
```

After changes, fill in Summary, Why, Files Changed, Features Added, Bugs Fixed, Tests Run, Known Issues, and Next Suggested Steps.

## Project Layout

```text
config/config.yaml
input/
output/
logs/
spool_house_ai/
  main.py
  config.py
  logging_setup.py
  pipeline.py
  gui.py
  test_mode.py
  watcher.py
  processing/
    analysis.py
    background.py
    preview.py
    silhouette.py
    stl.py
    vectorize.py
```

## Windows Setup

Install Python 3.12 from [python.org](https://www.python.org/downloads/windows/) and check "Add python.exe to PATH" during setup.

## Clone And Run

Clone the repository:

```powershell
git clone <repository-url>
cd "Spool House AI"
```

Create and activate a virtual environment:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
```

Install dependencies:

```powershell
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

Launch the desktop app:

```powershell
python -m spool_house_ai.gui
```

Launch the CLI watcher:

```powershell
python -m spool_house_ai.main
```

If PowerShell blocks activation, run:

```powershell
Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
```

## Run

Process existing images once:

```powershell
python -m spool_house_ai.main --once
```

Watch the input folder:

```powershell
python -m spool_house_ai.main --watch
```

Run the built-in V2 test:

```powershell
python -m spool_house_ai.main --test
```

Launch the V3 desktop app:

```powershell
python -m spool_house_ai.gui
```

Override product settings from the CLI:

```powershell
python -m spool_house_ai.main --once --product-mode keychain --threshold 145 --height 4.0 --stl-backend raster_heightfield --debug
```

## Product Modes

- `flat_relief`: general raised artwork relief for simple signs, emblems, and badges.
- `keychain`: adds keychain-oriented thickness behavior and can add a configurable keyring loop/hole.
- `wall_art`: creates a thicker display piece with stronger relief height.

## Detail Modes

- `silhouette_only`: old simple silhouette behavior.
- `preserve_holes`: keeps true holes and negative spaces as cutouts.
- `raised_details`: keeps the body as the printable base and raises thin internal dark lines on top.
- `engraved_details`: keeps the body as the printable base and lowers thin internal dark lines into the top surface.
- `layered_color_relief`: attempts to separate major color regions into stepped heights for color swaps or AMS-style experiments.

## Outputs

For `example.png`, Spool House AI writes:

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
  mesh_report.json
  job_settings.yaml
```

The contour debug preview uses:

- dark gray: kept printable mask
- gold: holes or negative spaces
- blue: internal details
- green: kept foreground contours
- red: removed tiny artifacts

## Main Config Settings

Edit `config/config.yaml` for defaults:

```yaml
pipeline:
  product_mode: flat_relief
  detail_mode: preserve_holes
  background_removal_enabled: false

silhouette:
  threshold_value: 128
  smoothing_enabled: true
  smoothing_strength: 3
  min_contour_area: 25
  simplify_tolerance: 1.5
  preserve_holes: true
  preserve_internal_details: true
  detail_mode: preserve_holes
  detail_height_mm: 0.8
  engraving_depth_mm: 0.6

stl:
  stl_backend: raster_heightfield
  product_mode: flat_relief
  detail_mode: preserve_holes
  output_scale_mm: 100.0
  base_height_mm: 1.6
  extrusion_height_mm: 3.0
  detail_height_mm: 0.8
  engraving_depth_mm: 0.6
  add_keychain_hole: false
  keychain_hole_diameter_mm: 5.0
```

`stl_backend` supports:

- `raster_heightfield`: default, safest backend, preserves existing SHAI behavior.
- `vector_extrusion`: optional contour extrusion backend for simple silhouette/hole-preserving jobs. If optional polygon extrusion support is unavailable or the selected product/detail mode is not supported, SHAI falls back to `raster_heightfield`.

## Geometry Quality / Smoothing Settings

V4/V5 improves jagged edges by tracing from cleaned, smoothed contours instead of directly exporting raw pixel stairs. V5 adds smart vector cleanup that straightens long nearly-straight runs, smooths curve sections conservatively, removes tiny floating islands, and keeps detail masks available for review.

Important settings in `config/config.yaml`:

```yaml
silhouette:
  upscale_factor: 2
  pre_blur_radius: 1
  adaptive_threshold: false
  morphology_enabled: true
  morphology_kernel_size: 5
  contour_smoothing_enabled: true
  contour_smoothing_strength: 1
  collinear_merge_tolerance: 2.0
  sharp_corner_angle_threshold: 35.0
  safe_smoothing_enabled: true
  smoothing_profile: conservative
  max_area_change_percent: 10
  max_bbox_change_percent: 10
  max_aspect_ratio_change_percent: 10
  max_point_reduction_percent: 80
  straight_line_cleanup_enabled: true
  straight_line_tolerance: 4.0
  min_straight_segment_length_px: 24
  curve_fit_enabled: true
  curve_fit_tolerance: 1.0
  min_curve_segment_length_px: 12
  max_curve_error_percent: 5
  remove_small_islands: true
  min_island_area_px: 75
  preserve_islands_near_body: true
  island_near_body_distance_px: 8

svg:
  vectorizer_backend: opencv

stl:
  curve_sample_resolution: 2
```

Use `upscale_factor: 4` for cleaner curves on small logos, at the cost of slower processing. Increase `simplify_tolerance` and `collinear_merge_tolerance` to clean straight logo edges; lower them if small corners disappear. `vectorizer_backend` can be set to `potrace` or `inkscape`, but the app falls back to OpenCV tracing when those tools are not installed.

Safe smoothing is enabled by default. The `conservative` profile rejects contour cleanup that changes area, bounding box, aspect ratio, or removes too many points. If a contour fails those checks, Spool House AI falls back to the less-smoothed contour instead of turning the artwork into a blob.

Each job folder includes V4 comparison previews:

```text
raw_threshold.png
raw_contours.png
smoothed_contours.png
final_vector_preview.png
geometry_before_after_overlay.png
geometry_report.txt
```

V5 also saves review comparisons:

```text
original_vs_cleaned_compare.png
original_vs_body_mask_compare.png
original_vs_detail_mask_compare.png
original_vs_final_vector_compare.png
original_vs_stl_preview_compare.png
removed_islands_debug.png
```

The GUI includes a simple Review panel after generation. Use the dropdown to compare original, cleaned, body, holes, details, vector, and STL preview stages side-by-side. The panel also shows `geometry_report.txt` and warns when smoothing fallback was used.

## Troubleshooting

- If `python` launches Inkscape's embedded Python, install regular Python 3.12 from python.org and enable "Add python.exe to PATH".
- If `python -m spool_house_ai.gui` says `Missing GUI dependency: PySide6`, activate your venv and run `python -m pip install -r requirements.txt`.
- If PowerShell blocks venv activation, run `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`.
- Background removal is disabled by default for responsiveness. Turn on `background_removal_enabled` only after `rembg` and its model are installed locally.

## Notes

- Transparent PNGs skip `rembg` and are processed directly.
- If background removal is disabled or unavailable, opaque images are copied as cleaned PNGs and the rest of the pipeline still runs.
- If STL generation fails, cleaned PNG, silhouette PNG, SVG, and debug previews are kept.
- SVG output includes an editable `artwork` group plus `edit-guides` contour paths for easier inspection in Inkscape.
