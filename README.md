# Spool House Studio

Spool House Studio automates image-to-product preparation for simple 3D printable STL files. It is part of the Spool House AI / SHAI project and keeps the internal `spool_house_ai` package name for compatibility.

Created by ChronicLand420.

## Current Workflow

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
   - `lithophane` (experimental)
8. Save cleaned PNG, silhouette PNG, SVG, STL, generic 3MF, final preview, and stage previews.

Future features such as AI image generation, Blender automation, mesh repair, dashboard, queueing, database, upload packages, slicer project generation, automatic slicing, and G-code export are intentionally not implemented yet.

## Desktop App

Launch the GUI:

```powershell
python -m spool_house_ai.gui
```

Create a Windows desktop shortcut for the GUI:

```powershell
python scripts/create_desktop_shortcut.py
```

This creates `Spool House Studio` on the current user's Desktop and points it at this repository with the repo root as the working directory. The source app also sets the Spool House icon for the running window/taskbar when launched with `python -m spool_house_ai.gui`.

The app supports:

- Drag/drop PNG or JPG files into the queue.
- Click `Add Image` to browse for files.
- Choose an `Artwork style` preset and `Product Setup` options.
- Open `Advanced Settings` only when you need backend, dimension, cleanup, vector, or keychain controls.
- Click `Generate` for the selected/first queued file, or `Generate All` to process the queue one image at a time.
- Watch rooms light up as stages run: Intake Room, Cleanup Lab, Detail Analyzer, Vector Workshop, Mesh Forge, Render Bay, and Output Vault.
- See elapsed time, rough ETA, and batch item count in the status strip while jobs run.
- Use `Output Vault` and `Production Review` to inspect generated files and preview thumbnails.
- Review the local artwork recommendation for preset and finished thickness, then click `Apply Recommendation` only if you want SHS to use it.
- Open the output folder, STL, 3MF, SVG, or preview directly when you need a specific artifact.
- Use `Open STL` or `Open 3MF` to open that exact model file in the configured slicer.

The header `Settings` button controls UI-only preferences such as dark/light theme, accent color, density, preview size, startup log behavior, output folder, slicer handoff preference, and optional post-generation actions. The Spool House Orange accent uses the official logo orange while keeping the internal preference value `orange` for compatibility. These preferences are stored separately from production pipeline settings in `config/ui_preferences.json`, which is ignored by Git.

The slicer handoff supports System default, OrcaSlicer, and Bambu Studio launch modes. It only opens the selected STL or validated generic 3MF file; it does not slice, export G-code, modify printer profiles, modify filament profiles, or inject filament-change markers. Optional OrcaSlicer and Bambu Studio executable paths can be configured in Settings.

The Settings/About area also has optional Support / Contact buttons. They are disabled until real links are configured in `spool_house_ai/app_identity.py` through `APP_SUPPORT_URL`, `APP_CONTACT_URL`, `APP_CONTACT_EMAIL`, or `APP_GITHUB_URL`. Donations are optional; the app has no ads, tracking, export limits, or paywall logic.

## Branding Assets

Spool House Studio keeps the official source logo separate from generated app assets:

```text
assets/branding/spool_house_logo_source.png
assets/branding/spool_house_logo_gui.png
assets/branding/spool_house_icon.png
assets/branding/spool_house_icon.ico
assets/branding/spool_house_wordmark_icon.png
assets/branding/spool_house_wordmark_icon.ico
```

The full logo is used in the GUI header and Settings/About area. The full wordmark icon is used for desktop shortcuts and the portable EXE/File Explorer identity. The simplified logo-only icon is used by the running Qt app for window/taskbar-sized runtime icons where Windows honors the application icon separately from the embedded EXE icon.

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

Check that the active Python environment is the one you expect:

```powershell
python scripts/check_environment.py
```

On Windows, prefer the virtual environment Python explicitly if `python` resolves to another app's bundled runtime:

```powershell
.\.venv\Scripts\python.exe scripts/check_environment.py
.\.venv\Scripts\python.exe -m spool_house_ai.gui
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

## Portable Windows Build

Portable EXE packaging is build-only and uses separate build dependencies so normal source users do not need PyInstaller:

```powershell
python -m pip install -r requirements-build.txt
python scripts/build_portable_windows.py
```

By default, the build helper writes to a temp review folder:

```text
%TEMP%\shai_spool_house_studio_build\dist\Spool House Studio\
```

The helper creates a one-folder portable app, copies `assets/`, copies `config/config.yaml`, creates `input/`, `output/`, and `logs/`, and uses `assets/branding/spool_house_wordmark_icon.ico` for the EXE icon. Do not commit `build/`, `dist/`, `release/`, generated EXE files, local outputs, logs, or `config/ui_preferences.json`.

To inspect the PyInstaller command without building:

```powershell
python scripts/build_portable_windows.py --dry-run
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

Run the built-in pipeline test:

```powershell
python -m spool_house_ai.main --test
```

Launch the desktop app:

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
- `lithophane`: experimental flat lithophane panel that maps image brightness to plastic thickness.
- `filament_swap_relief`: experimental stepped-height relief for manual filament swaps from flat color artwork.

### Lithophane (Experimental)

Lithophane mode creates a flat rectangular STL from photo brightness instead of tracing logo contours. Bright or white pixels are thinner by default, and dark pixels are thicker, so the image reads correctly when backlit. Cleanup presets, SVG tracing, detail handling, and vector/raster backend choices do not apply to lithophane jobs.

Lithophane crispness controls are available in Advanced Settings when Lithophane is selected. Defaults are intentionally neutral: autocontrast is off, contrast and gamma are `1.0`, sharpening is `0.0`, and denoise radius is `0`, so existing output stays close to previous behavior. Increase contrast or sharpening carefully for crisper details, use gamma to tune midtones, and use light denoise only when noisy photos create rough thickness changes. Lithophane jobs save a processed grayscale preview so you can inspect the exact image used for thickness mapping.

This first version is intentionally simple: flat panels only, no curved lamp shades, no sockets, no stands, no color lithophanes, and no AI cleanup. It works best with clear, bright, high-contrast images. For printing, start with light-colored or white PLA and a small layer height, then verify orientation and exposure in your slicer.

### Filament Swap Relief (Experimental)

Filament Swap Relief turns flat color artwork into one solid stepped-height STL for manual filament changes. Each detected printable color becomes a different final height. By default, light colors print lower and dark colors print higher, so a three-color white/red/black design becomes roughly:

- Start with the light color from `0.00 mm` to `0.80 mm`
- Change before the layer that starts at `0.80 mm`
- Change before the layer that starts at `1.20 mm`
- Final height is `1.60 mm`

This is a single solid layer-cake model, not true per-layer multicolor, G-code, MMU/AMS output, or separate STL files per color. It works best with clean logos, decals, signs, anime-style title art, and other flat-color artwork. Cleanup presets, SVG tracing, detail handling, and vector/raster backend choices do not apply to this mode. No AI is used. Use artwork you own or have permission to print.

Manual swap planning is layer-aware. Set the first-layer height, normal layer height, and alignment mode in the Filament Swap Relief controls. Reports use one-based layer numbers: “Change before layer N” means finish layer `N-1`, pause the printer, load the next filament, and print layer `N` with the new filament. With default `0.20 mm` first/normal layers, the default `0.80 / 0.40 mm` heights remain unchanged. If custom heights do not land on real layer starts, Spool House Studio can snap them upward, snap to nearest, or reject them in strict mode.

Filament Swap Relief has its own palette and island controls. RGB palette clustering remains the default for backward-compatible output, while LAB clustering can be selected experimentally for more perceptual color grouping. Island Handling controls what happens to disconnected color components after segmentation:

- `preserve_all`: keeps every post-segmentation component and reports tiny pieces as intentionally preserved.
- `remove_below_threshold`: default behavior; removes components smaller than `min_region_area_px`.
- `merge_with_nearest_region`: reassigns small components to the nearest printable region using deterministic contact/distance rules.
- `connect_within_maximum_gap`: connects small components to a same-color kept component when it is within the configured gap.

Island handling cannot recover details already lost during downsampling, smoothing, palette clustering, or background removal. The reports distinguish those earlier steps from island actions. Filament jobs write island review previews such as `_filament_islands_detected.png`, `_filament_islands_actions.png`, and action-specific preserved/removed/merged/connected previews when those actions occur. Filament jobs also write `reports/color_plan.json` and `reports/filament_swap_plan.txt`, which contain the layer-aware manual swap plan used by the STL and generic 3MF geometry.

Every successful STL job also writes `3mf/<stem>.3mf` as a minimal standards-compliant generic 3MF model. This 3MF keeps the same size, orientation, and geometry as the STL, but it is not an OrcaSlicer or Bambu Studio project and does not embed filament-change markers, printer profiles, process profiles, or G-code. For Filament Swap Relief, use the separate `reports/filament_swap_plan.txt` for manual filament-change instructions.

## Detail Modes

- `silhouette_only`: old simple silhouette behavior.
- `preserve_holes`: keeps true holes and negative spaces as cutouts.
- `raised_details`: keeps the body as the printable base and raises thin internal dark lines on top.
- `engraved_details`: keeps the body as the printable base and lowers thin internal dark lines into the top surface.
- `layered_color_relief`: attempts to separate major color regions into stepped heights for color swaps or AMS-style experiments.

## Outputs

For `example.png`, Spool House Studio writes:

```text
output/example/
  source/
    example.png
  svg/
    example.svg
    example_review.svg
  stl/
    example.stl
  3mf/
    example.3mf
  previews/
    example_cleaned.png
    example_silhouette.png
    example_body_mask.png
    example_hole_mask.png
    example_detail_mask.png
    example_contour_debug.png
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
  reports/
    mesh_report.json
    job_status.json
    job_summary.md
    job_settings.yaml
```

`example.svg` is the normal editable vector output. `example_review.svg` adds visible inspection layers for foreground/body contours, holes, preserved details, and ignored islands so the artwork is easier to inspect in Inkscape before STL export.

For lithophane jobs, the same job folder structure is used, but SVG files are not created because lithophane output is generated from grayscale brightness. The STL, generic 3MF, preview, mesh report, job status, and job summary are still written under `stl/`, `3mf/`, `previews/`, and `reports/`.

Lithophane jobs also write `previews/<stem>_lithophane_processed.png`, which shows the grayscale image after any crispness preprocessing and before thickness mapping. `job_status.json` and `job_summary.md` record the Lithophane preprocessing settings; `mesh_report.json` remains focused only on mesh health.

For Filament Swap Relief jobs, SVG files are also not created in this first version. Instead, previews include color-group and height-map images, `stl/<stem>.stl` contains the printable STL, `3mf/<stem>.3mf` contains the matching generic 3MF model, and `reports/filament_swap_report.json`, `reports/color_plan.json`, `reports/filament_swap_plan.txt`, and `job_summary.md` include the detected color order, aligned layer heights, and manual swap instructions. The generic 3MF is model geometry only; filament-change instructions remain separate.

The default output root is `output/`. In the GUI, use `Settings` -> `Output Folder` to choose a different root folder. New jobs still use the same per-image pattern, `<selected output root>/<input stem>/`, and then place files into the `source/`, `svg/`, `stl/`, `3mf/`, `previews/`, and `reports/` subfolders. CLI runs continue to use `config/config.yaml` unless you change that config directly.

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
  cleanup_preset: default
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
  stl_backend: auto_vector_first
  product_mode: flat_relief
  detail_mode: preserve_holes
  output_scale_mm: 100.0
  base_height_mm: 1.6
  extrusion_height_mm: 3.0
  detail_height_mm: 0.8
  engraving_depth_mm: 0.6
  add_keychain_hole: false
  keychain_hole_diameter_mm: 5.0
  lithophane_width_mm: 100.0
  lithophane_min_thickness_mm: 0.8
  lithophane_max_thickness_mm: 3.0
  lithophane_invert: false
  lithophane_max_pixels: 60000
  lithophane_autocontrast_enabled: false
  lithophane_autocontrast_cutoff_percent: 0.5
  lithophane_contrast: 1.0
  lithophane_gamma: 1.0
  lithophane_sharpen_strength: 0.0
  lithophane_denoise_radius_px: 0

filament_swap_relief:
  width_mm: 120.0
  color_count: 3
  base_height_mm: 0.8
  layer_step_mm: 0.4
  first_layer_height_mm: 0.20
  layer_height_mm: 0.20
  height_alignment_mode: snap_up
  height_alignment_tolerance_mm: 0.001
  auto_background_ignore: true
  background_border_sample_px: 12
  background_confidence_threshold: 0.45
  max_sampled_pixels: 90000
  min_region_area_px: 30
  smooth_edges: true
  edge_smoothing_px: 1
  color_order: light_to_dark
  palette_color_space: rgb
  palette_random_seed: 17
  island_policy: remove_below_threshold
  island_merge_max_distance_px: 8
  island_merge_fallback: remove
  island_connect_max_gap_px: 3
  island_connection_width_px: 1
  island_connect_fallback: remove
  island_report_components: true
```

`stl_backend` supports:

- `auto_vector_first`: default for logo/wall-art style jobs. SHAI tries contour-based vector extrusion first and falls back to `raster_heightfield` when vector extrusion is unavailable or unsupported.
- `vector_extrusion`: experimental contour extrusion backend for simple silhouette/hole-preserving jobs.
- `raster_heightfield`: stable raster fallback that preserves existing SHAI behavior.

`mesh_report.json` records the requested backend, actual backend, fallback reason, watertight status, edge counts, warnings, and failures.

## Cleanup Presets / Artifact Reporting

The GUI exposes cleanup presets in the Presets panel:

- `default`: balanced behavior for mixed artwork; preserves nearby small islands when they may be intentional details.
- `clean_logo`: removes tiny isolated dot artifacts more aggressively for simple logos, wall art, Nike/Mopar-style artwork, clean marks, and bold text logos.
- `detail_preserving`: keeps more small detached or near-body detail for artwork where tiny pieces may matter.
- `drip_logo`: removes far-away specks while preserving nearby drips, drops, and small detached logo pieces.
- `splatter_logo`: preserves rough/splatter edges and near-body texture while still removing tiny isolated junk.
- `line_art`: preserves long outline strokes, sneaker panels, coloring-page lines, tattoo-flash outlines, and clean interior linework while reducing far-away specks.
- `preserve_floating_islands`: preserves intentional detached dots, stars, eye highlights, earrings, accents, and multipart artwork by disabling automatic island removal.

Use Clean Logo when a logo or wall-art input has unwanted floating dots, specks, or small detached islands. Use Line Art for sneaker outlines, coloring-page drawings, tattoo-flash style artwork, technical outlines, and artwork where interior strokes matter. Use Drip / Graffiti for drip marks where nearby drops are part of the design. Use Splatter / Rough for distressed or rough logos where edge texture matters. Use Preserve Floating Islands when detached dots are intentional details, such as stars, stippling, sparkles, distressed texture, eye highlights, earrings, or small decorative marks. For Filament Swap Relief, use `Island handling = Preserve all` instead of the Wall Art cleanup preset.

The GUI includes a deterministic local recommendation helper near the preset controls. It measures the selected artwork's mask, components, holes, contour density, edge density, roughness, narrow-feature percentage, and drip/splatter signals, then suggests a cleanup preset and finished model thickness. This is not AI, does not use the internet, and never changes settings automatically; click `Apply Recommendation` to accept it or keep your own choices.

Each `job_status.json` includes an `artifact_summary` section with artwork cleanup counts such as isolated, removed, and preserved islands. `mesh_report.json` stays focused on STL mesh health; `artifact_summary` is about artwork cleanup quality before export. `job_summary.md` is a short human-readable package summary for slicer/product review.

## Geometry Quality / Smoothing Settings

V4/V5 improves jagged edges by tracing from cleaned, smoothed contours instead of directly exporting raw pixel stairs. V5 adds smart vector cleanup that straightens long nearly-straight runs, smooths curve sections conservatively, removes tiny floating islands, and keeps detail masks available for review.

Important settings in `config/config.yaml`:

```yaml
silhouette:
  cleanup_preset: default
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

Safe smoothing is enabled by default. The `conservative` profile rejects contour cleanup that changes area, bounding box, aspect ratio, or removes too many points. If a contour fails those checks, Spool House Studio falls back to the less-smoothed contour instead of turning the artwork into a blob.

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

- If `python` launches Inkscape's embedded Python, install regular Python 3.12 from python.org and enable "Add python.exe to PATH", or run commands with `.\.venv\Scripts\python.exe`.
- Run `python scripts/check_environment.py` to see the active Python executable and whether `cv2`, `PySide6`, `shapely`, `mapbox_earcut`, and `config/config.yaml` load correctly.
- If `python -m spool_house_ai.gui` says `Missing GUI dependency: PySide6`, activate your venv and run `python -m pip install -r requirements.txt`.
- If PowerShell blocks venv activation, run `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`.
- Background removal is disabled by default for responsiveness. Turn on `background_removal_enabled` only after `rembg` and its model are installed locally.

## Notes

- Transparent PNGs skip `rembg` and are processed directly.
- If background removal is disabled or unavailable, opaque images are copied as cleaned PNGs and the rest of the pipeline still runs.
- If STL generation fails, cleaned PNG, silhouette PNG, SVG, and debug previews are kept.
- SVG output includes structured `foreground_mask`, `main_body`, `holes`, `preserved_details`, and `ignored_islands` groups. Each job also writes a visible `_review.svg` for easier inspection in Inkscape.
