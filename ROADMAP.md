# Roadmap

Spool House Studio's near-term direction is to make every generated file feel obviously ready to print. New features are less important than cleaner geometry, safer defaults, clearer printability decisions, and reliable handoff to common slicers.

## Current Priorities

1. **Filament Relief quality tuning**
   - Smooth color-region curves and edges so filament-swap relief output looks intentional instead of pixel-stepped.
   - Keep improving contour quality without damaging the recent vector-extrusion robustness and minimum printability systems.
   - Preserve the stack/building-block behavior for color swaps unless the user explicitly chooses a future engraved/debossed style.

2. **Printer/nozzle-aware defaults**
   - Move beyond fixed defaults by deriving practical values from nozzle size, line width, layer height, and printer type.
   - Keep the current `0.4 mm` nozzle / Ender-5 S1 style defaults sensible while preparing for broader "any printer" use.

3. **Clearer print-safe cleanup UX**
   - Make Advanced printability controls friendlier for normal users.
   - Prefer plain labels such as `Print-safe cleanup`, `Minimum printable detail`, `Remove tiny unprintable pieces`, and `Strengthen thin lines`.

4. **Visual warnings before final export**
   - Show what Spool House Studio plans to remove, thicken, fill, merge, or connect before the user prints.
   - Add trust-building overlays, especially for Filament Relief and minimum printable geometry cleanup.

5. **Filament swap instructions polish**
   - Turn existing reports into a shorter shop-ready checklist.
   - Include layer changes, colors, thickness, slicer layer height, and a clear reminder not to rescale after generation.

6. **Slicer-ready color project export**
   - Keep validated generic 3MF export as the safe default handoff format.
   - Add an optional slicer-specific project export for Filament Relief so OrcaSlicer or Bambu Studio can open with color/filament assignments already present.
   - Use the existing Filament Relief color plan and manual swap plan as the source of truth.
   - Do not generate G-code, send prints, overwrite slicer profiles, or replace the generic 3MF path.
   - Prototype OrcaSlicer first, then add Bambu Studio only after the Orca project export is stable.

7. **QA artifact cleanup**
   - Keep large local QA output such as `Preset QA/` out of commits.
   - Consider moving long-lived QA archives outside the repository or tightening ignore rules for generated QA folders.

8. **Portable rebuild**
   - Rebuild and refresh the portable app after source-level changes are reviewed and committed.
   - Treat this as release hygiene rather than a source-code feature.

## Guardrails

- Keep the app local, deterministic, and easy to understand.
- Do not add AI, cloud services, ads, license checks, paywalls, or hidden internet requirements.
- Do not prioritize complicated slicer automation until STL and generic 3MF output quality is consistently print-ready.
- Keep generic 3MF as the safe slicer handoff format.
- Keep manual filament-swap workflows friendly for older printers and Klipper-style setups where the user changes filament by hand.

## Later Ideas

- Richer printer profiles and material profiles.
- Optional slicer-project export only after format research and prototypes prove it is safe.
- More product modes once the current print-readiness foundation is consistently strong.
