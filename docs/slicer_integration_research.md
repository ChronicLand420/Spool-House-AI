# Spool House Studio Slicer Integration Research

Date: 2026-07-13

Scope: research only. This document records generic 3MF, OrcaSlicer, and Bambu Studio automation findings for future Spool House Studio phases. It does not approve Orca/Bambu project export, slicer marker injection, G-code generation, printer-profile generation, or automatic slicing.

## Summary Recommendation

Keep generic 3MF as the canonical slicer handoff format for now. It is standards-based, small, and preserves Spool House Studio geometry without vendor metadata. Future OrcaSlicer or Bambu Studio project export should be optional and built on top of the generic 3MF exporter, not a replacement for it.

The first safe UI improvement is now `Open in Slicer`:

- prefer the validated generic 3MF
- fall back to STL when the 3MF is missing or failed validation
- support System default, OrcaSlicer, and Bambu Studio launch modes
- launch slicers with the selected model as a single positional argument
- do not slice, export G-code, modify profiles, or generate slicer-specific project files

Do not generate slicer-specific project files until a separate prototype validates the exact project structure against controlled Orca/Bambu reference files.

## Open in Slicer Implementation Findings

Verified locally / implemented:

- System default mode opens the selected `.3mf` or `.stl` through the operating-system file association.
- OrcaSlicer mode launches `orca-slicer.exe` with exactly one model-file argument.
- Bambu Studio mode launches `bambu-studio.exe` with exactly one model-file argument.
- The launch path uses an argument list, not a shell command string.
- No slicing, export, profile, printer, filament, or G-code flags are passed by SHS.
- Slicer executable paths are UI preferences stored in ignored `config/ui_preferences.json`, not committed production config.
- Discovery checks configured path, common install locations, PATH, and bounded Windows App Paths registry entries.
- Discovery does not perform a whole-disk search and does not hardcode a Windows user name.
- Bambu Studio `--info <model>` is treated as the only currently enabled read-only CLI diagnostic because local Bambu help confirms `--info`.
- OrcaSlicer read-only diagnostics remain disabled in SHS because local `--help` did not produce reliable captured output and the useful `--info` behavior was not present in official documentation reviewed during this pass.
- Temporary and self-test SHS generic 3MF files opened through Bambu Studio `--info` returned model dimensions (`size_x`, `size_y`, `size_z`), manifold status, facet count, and part count without slicing or writing G-code. Bambu may also print internal plate-triangle log noise to stdout even when the command exits successfully, so SHS treats this as diagnostic text rather than visual UI verification.

Verified common executable locations:

- OrcaSlicer:
  - `C:\Program Files\OrcaSlicer\orca-slicer.exe`
  - `%LOCALAPPDATA%\Programs\OrcaSlicer\orca-slicer.exe`
- Bambu Studio:
  - `C:\Program Files\Bambu Studio\bambu-studio.exe`
  - `%LOCALAPPDATA%\Programs\Bambu Studio\bambu-studio.exe`

Requires manual confirmation:

- Visual confirmation that OrcaSlicer and Bambu Studio display the opened model at the expected size and orientation.
- Whether the user's Windows file association opens the preferred slicer for `.3mf` or `.stl`.
- Whether a slicer UI shows a repair warning after launch; SHS does not automate UI inspection.

Unsupported in this phase:

- automatic slicing
- G-code export
- printer/process/filament profile selection
- AMS slot selection
- manual filament-change marker injection
- Orca/Bambu project 3MF generation
- GUI-clicking automation

## Sources

Verified local binaries:

- `C:\Program Files\Bambu Studio\bambu-studio.exe`, local help output: `BambuStudio-02.04.00.70`
- `C:\Program Files\OrcaSlicer\orca-slicer.exe`, local binary found, but `--help` produced no captured output in PowerShell during this pass.

Reference project inspected:

- Orca/Bambu-style project 3MF: `C:\Users\Christian\Desktop\Spool House Studio Portable\output\Butterfly Flower\stl\Butterfly Flower.3mf`
- Matching SHS STL: `C:\Users\Christian\Desktop\Spool House Studio Portable\output\Butterfly Flower\stl\Butterfly Flower.stl`

Web references:

- Bambu Studio command line wiki: https://github.com/bambulab/BambuStudio/wiki/Command-Line-Usage
- OrcaSlicer import/export wiki: https://www.orcaslicer.com/wiki/general_settings/import_export
- OrcaSlicer CLI discussion #8593: https://github.com/OrcaSlicer/OrcaSlicer/discussions/8593
- OrcaSlicer CLI issue #8155: https://github.com/OrcaSlicer/OrcaSlicer/issues/8155
- OrcaSlicer plugin host API: https://www.orcaslicer.com/wiki/developer_reference/plugin_development/api_reference/host
- 3MF Core Specification: https://github.com/3MFConsortium/spec_core/blob/master/3MF%20Core%20Specification.md
- Bambu third-party integration overview: https://wiki.bambulab.com/en/software/third-party-integration

## 3MF Baseline

Verified from the 3MF Core Specification:

- A 3MF document is a ZIP/Open Packaging Conventions package.
- A primary 3D payload is rooted through a StartPart relationship that points to the 3D model part.
- A valid model part contains resources/objects and a build section.
- The generic SHS exporter should stay minimal:
  - `[Content_Types].xml`
  - `_rels/.rels`
  - `3D/3dmodel.model`
  - `unit="millimeter"`
  - direct vertices/triangles
  - one object
  - one build item

Do not include vendor/project entries in the generic exporter.

## Butterfly Flower Reference Project

File hashes:

- Reference 3MF size: `345011` bytes
- Reference 3MF SHA-256: `E4DD8C2DC0DCA1CABBA245E5827BF8C8DEF988734057710C1B64CF5614386DA6`
- Matching STL size: `975484` bytes
- Matching STL SHA-256: `208346F649745F28194D516AE11C0EF88342E3B04487D5F10777AF6AB329EBE9`

Archive entries:

```text
[Content_Types].xml
Metadata/plate_1.png
Metadata/plate_1_small.png
Metadata/plate_no_light_1.png
Metadata/top_1.png
Metadata/pick_1.png
Metadata/plate_1.json
3D/3dmodel.model
3D/_rels/3dmodel.model.rels
3D/Objects/Butterfly Flower.stl_1.model
Metadata/project_settings.config
Metadata/model_settings.config
Metadata/slice_info.config
_rels/.rels
```

Findings:

- This is not a minimal generic 3MF.
- It is an unsliced Bambu/Orca project-style 3MF.
- The app metadata identifies `BambuStudio-2.3.2`.
- The raw component mesh matched the SHS STL dimensions exactly in the prior inspection.
- Project-level transforms changed effective size and plate placement.
- Vendor/project entries include thumbnails, plate metadata, project settings, model settings, and slice info.
- No manual filament-change marker schedule was identified in this reference file.
- It contains slicer project/profile metadata; the generic SHS exporter must not copy this structure.

## Bambu Studio CLI Capabilities

Verified locally from `bambu-studio.exe --help` and consistent with the Bambu Studio wiki.

Input:

- Accepts `file.3mf` and `file.stl` positional inputs.
- Can load multiple files by passing multiple inputs.

Settings and profiles:

- `--load-settings "setting1.json;setting2.json"`: load process/machine settings.
- `--load-filaments "filament1.json;filament2.json;..."`: load filament settings.
- `--load-filament-ids "1,2,3,1"`: map filament IDs to objects.
- `--load-defaultfila option`: load first filament as default for missing filament settings.
- `--uptodate-settings`, `--uptodate-filaments`, `--uptodate`: update configuration values.
- Settings priority from local help:
  1. command-line setting values
  2. settings loaded with `--load_settings` and `--load_filaments`
  3. settings loaded from 3MF

Placement and geometry:

- `--arrange option`
- `--orient`
- `--rotate`, `--rotate-x`, `--rotate-y`
- `--scale factor`
- `--convert-unit`
- `--ensure-on-bed`
- `--allow-rotations`
- `--clone-objects`
- `--skip-objects`
- `--allow-multicolor-oneplate`
- `--avoid-extrusion-cali-region`
- `--assemble`
- `--repetitions count`

Slicing/export:

- `--slice option`: 0 for all plates, `i` for plate `i`.
- `--export-3mf filename.3mf`: export project as 3MF.
- `--export-settings settings.json`
- `--export-slicedata slicing_data_directory`
- `--export-stl`
- `--export-stls`
- `--export-png option`
- `--load-slicedata slicing_data_directory`

Other:

- `--debug level`
- `--outputdir dir`
- `--info`
- `--pipe pipename`
- `--no-check`
- `--normative-check option`
- `--allow-newer-file option`
- `--min-save option`
- `--mstpp time`
- `--mtcpp count`
- `--load-custom-gcodes custom_gcode_toolchange.json`
- `--load-assemble-list assemble_list.json`
- `--metadata-name`, `--metadata-value`
- `--makerlab-name`, `--makerlab-version`

Verified from Bambu wiki examples:

- Slice 3MF directly using settings inside the 3MF.
- Slice 3MF with explicit machine/process/filament JSON.
- Slice STL with `--orient`, `--arrange`, `--load-settings`, `--load-filaments`, `--slice`, and `--export-3mf`.

Unsupported or not verified:

- No documented Bambu Studio CLI flag was found for directly adding manual filament-change markers to a project without slicing.
- No documented flag was found for choosing AMS slots by user-friendly name beyond filament IDs/configs.
- No documented headless/silent guarantee was found beyond CLI operation; GUI dependencies may still initialize.

## OrcaSlicer CLI Capabilities

Verified locally:

- `C:\Program Files\OrcaSlicer\orca-slicer.exe` exists.
- `--help` did not produce captured help output in this PowerShell environment.

Verified from Orca discussion/issue references:

- Orca CLI can be called with a model or `.3mf` project file as the final argument.
- `--slice 0` slices all plates; other plate indexes can target a plate.
- Passing `--slice` with `--export-3mf` may produce a `.gcode.3mf` containing G-code.
- CLI can load JSON profiles and can use auto-orient/arrange behavior.
- `--curr-bed-type` can select a bed type such as `Textured PEI Plate`.
- Issue examples show `--debug`, `--export-slicedata`, `--outputdir`, `--slice`, `--load-filaments`, and `--load-settings`.

Known risks from issue/discussion references:

- Some external preset loading paths fail or are brittle depending on file type/config shape.
- Full CLI-only multi-object/project assembly has reported limitations and may still require GUI verification.

Unsupported or not verified:

- No reliable local help capture was available in this environment.
- No verified direct project marker injection support was found.
- No documented no-GUI/headless contract was found.

## Import/Export Behavior

Verified from OrcaSlicer import/export docs:

- OrcaSlicer imports models through File menu, toolbar add button, or drag/drop.
- Export Model supports STL, DRC, and Generic 3MF.
- Generic 3MF is explicitly described as model-only without printer, material, or process information.
- Project 3MF stores objects plus print settings/configurations.
- Sliced export can produce G-code or Gcode.3MF with toolpath data and print information.
- Orca may attempt to extract Printer/Material/Process information from some 3MF files; incompatible info is ignored.
- Orca notes that 3MF files can be opened as ZIP archives for inspection.

Practical interpretation for SHS:

- SHS generic 3MF should import as geometry.
- SHS should not expect generic 3MF to carry printer/process/filament profiles.
- SHS should keep color_plan.json and filament_swap_plan.txt separate until slicer-specific project support is researched and validated.

## Project 3MF Structure

Generic 3MF:

- Standards-based model package.
- Direct mesh object and build item are enough for SHS geometry.
- No printer/process/filament data.
- No slicer thumbnails.
- No G-code.

Orca/Bambu project 3MF:

- Still a ZIP/3MF package, but with slicer/vendor metadata.
- Can include object indirection under `3D/Objects/...`.
- Can include model relationships.
- Can include `Metadata/project_settings.config`, `Metadata/model_settings.config`, `Metadata/slice_info.config`, plate JSON, and thumbnails.
- Can include transforms that change plate placement or effective model display.
- Can include printer/process/filament settings.

Sliced `.gcode.3mf`:

- Project/package containing G-code/toolpaths and enough print context for sending/printing.
- Out of scope for SHS now.

## Filament Swap / Color Relief Possibilities

Verified:

- Bambu and Orca CLIs can slice/export project-style 3MF with settings.
- Orca discussion states `--slice` plus `--export-3mf` creates `.gcode.3mf` containing G-code.
- Bambu CLI accepts `--load-custom-gcodes`, but this is slicing/G-code-oriented, not verified as an unsliced project marker API.

Unknown:

- Whether unsliced Orca/Bambu project 3MFs can safely contain manual color-change markers without G-code generation.
- Whether a layer schedule can be represented as project metadata that the slicer UI will honor before slicing.
- Whether such marker metadata is stable across Orca/Bambu versions.

Recommendation:

- Continue exporting human-readable `filament_swap_plan.txt` and machine-readable `color_plan.json`.
- Do not inject pause/color-change markers into project files until an Orca-only prototype proves the exact fields.
- Treat Bambu project support as a later phase after Orca is stable.

## Plugin / API Support

OrcaSlicer:

- Has a documented plugin API.
- `orca.host` is read-only and intended for analysis, reporting, and export plugins.
- It exposes model, plater, preset bundle, objects/volumes/instances, mesh vertices/triangles, transforms, bounds, and manifold status.
- The documented host API does not mutate models. It is not currently a good fit for SHS to push geometry/settings into Orca from outside.

Bambu Studio:

- A public Python-style plugin API comparable to Orca's documented host API was not verified.
- Bambu provides a network/third-party integration direction for printer control/monitoring via Bambu Connect/network plugin, but that is printer-control scope, not project-file generation.
- Do not depend on printer network APIs for SHS model export.

## Future Automation Matrix

| Capability | OrcaSlicer | Bambu Studio | Notes |
| --- | --- | --- | --- |
| Open STL from shell | Likely by positional file | Verified by CLI usage | UI launch should be tested manually. |
| Open generic 3MF from shell | Likely by positional file | Verified by CLI usage | SHS can offer "Open in default slicer" safely first. |
| Import multiple models | Likely positional inputs | Verified positional inputs | Per-object settings are risky. |
| Select printer/process/filament | Likely via settings JSON | Verified via `--load-settings`/`--load-filaments` | Requires valid full config JSON. |
| Select bed/plate | `--curr-bed-type`, `--slice` via discussion | `--slice`; local help did not show `--curr-bed-type`, but wiki example uses it | Needs version-specific testing. |
| Arrange/orient | Verified from references | Verified locally | Use cautiously; it can change orientation/placement. |
| Export project 3MF | Verified from references | Verified locally | With slicing it may become Gcode.3MF. |
| Headless slicing | Likely CLI path | CLI path exists | Startup/runtime dependencies still need machine tests. |
| Manual swap markers without G-code | Unknown | Unknown | Do not implement yet. |
| Plugin/API model mutation | Not verified; host API read-only | Not verified | No SHS integration yet. |

## Open Button Architecture

Implemented architecture:

1. Main button: `Open in Slicer`.
2. File selection:
   - validated generic 3MF first
   - STL fallback when 3MF is unavailable or failed validation
   - optional preference to prefer STL while keeping 3MF available
3. Slicer selection:
   - System default
   - OrcaSlicer
   - Bambu Studio
4. Direct artifact buttons:
   - `Open STL`
   - `Open 3MF`
   - output folder/root actions
5. Configured executable paths:
   - OrcaSlicer executable path
   - Bambu Studio executable path
6. Launch syntax:
   - system default uses OS file association
   - Orca/Bambu launch with `[executable, model_path]`

Avoid:

- Calling `--slice` automatically.
- Generating G-code.
- Loading printer/filament profiles unless the user explicitly configures them.
- Hiding slicer warnings from the user.
- Treating a successful process launch as proof of visual/model correctness.

## Future Phases

1. Add `Open 3MF` / `Open in default slicer`.
2. Orca reference project research:
   - Generate controlled Orca projects from one SHS generic 3MF.
   - Inspect project metadata and transforms.
   - Create `docs/orca_3mf_format_research.md`.
3. Orca project prototype:
   - Standalone script, no GUI integration.
   - Validate archive structure and open in Orca manually.
4. Orca project export integration only after prototype passes.
5. Bambu Studio investigation after Orca support is stable.

## Release Decision

For the next release candidate, ship generic 3MF for every successful STL and keep slicer automation limited to safe file handoff. `Open in Slicer` may launch System default, OrcaSlicer, or Bambu Studio with the chosen generic 3MF/STL as a file argument only. Continue avoiding fragile vendor-specific project generation, G-code export, and marker injection until dedicated prototypes prove those formats safely.
