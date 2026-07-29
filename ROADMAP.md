# Roadmap

Spool House Studio's near-term direction is to make every generated file feel obviously ready to print. New features are less important than cleaner geometry, safer defaults, clearer printability decisions, and reliable handoff to common slicers.

## Recently Completed

- Added a shared minimum printable geometry system with customer-facing `Print-safe cleanup` controls.
- Added preview/report artifacts for printability actions so removed, filled, and adjusted geometry can be reviewed.
- Improved Filament Relief contour quality with upsampled vector contours and a lower default simplification tolerance.
- Added `Solid base plate` behavior for Filament Relief.
- Locked solid-base swap math to a `2.0 mm` base plate plus `0.8 mm` per artwork color band by default.
- Updated Filament Relief color-plan UI and reports so the base is shown as its own layer block and the first artwork color begins after a real swap.
- Added shop-ready filament swap checklist text to reports, including slicer layer-height and no-rescale reminders.
- Added printer/nozzle-aware print-safe defaults based on nozzle diameter, line width, and layer height.

## Current Priorities

1. **Filament Relief visual QA and final tuning**
   - Continue testing real-world signs, line art, and multi-color artwork in OrcaSlicer.
   - Keep improving color-region curves and diagonal lines when visual QA exposes remaining pixel stepping.
   - Preserve the stack/building-block behavior by default.
   - Keep the optional `Engraved / recessed` style available for intentional recessed designs.

2. **Visual warnings before final export**
   - Show what Spool House Studio plans to remove, thicken, fill, merge, or connect before the user prints.
   - Add trust-building overlays, especially for Filament Relief and minimum printable geometry cleanup.
   - Make warnings understandable without requiring users to read JSON reports.
   - Current jobs already write and surface a print-safe cleanup preview; the next step is making that review more guided.

3. **Slicer-ready color project export**
   - Keep validated generic 3MF export as the safe default handoff format.
   - Add an optional OrcaSlicer project export so Filament Relief files can open with color/filament assignments already present.
   - Use a real Orca-saved project as the reference format instead of relying on generic 3MF material display colors.
   - Consider Bambu Studio compatibility after the Orca project export is stable.
   - Use the existing Filament Relief color plan and manual swap plan as the source of truth.
   - Do not generate G-code, send prints, overwrite slicer profiles, or replace the generic 3MF path.

4. **Printer profile presets**
   - Add friendly preset choices for common nozzle sizes and printer styles.
   - Keep manual values available for Klipper/older printers and unusual line-width setups.

5. **QA artifact cleanup**
   - Keep large local QA output such as `Preset QA/` out of commits.
   - Consider moving long-lived QA archives outside the repository or tightening ignore rules for generated QA folders.

6. **Portable rebuild**
   - Rebuild and refresh the portable app after source-level changes are reviewed and committed.
   - Treat this as release hygiene rather than a source-code feature.
   - Verify both the raw/development launcher and portable launcher after the rebuild.

7. **Release-candidate smoke pass**
   - Run fresh Wall Art, Lithophane, and Filament Relief jobs from the rebuilt portable app.
   - Confirm STL and generic 3MF output open cleanly in OrcaSlicer.
   - Confirm no G-code is generated automatically and no printer action is triggered.

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
