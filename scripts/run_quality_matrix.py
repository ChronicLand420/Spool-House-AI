from __future__ import annotations

import argparse
import csv
import json
import logging
import shutil
import sys
import tempfile
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from spool_house_ai.config import AppConfig, apply_cleanup_preset, load_config
from spool_house_ai.pipeline import ImagePipeline
from spool_house_ai.test_mode import (
    create_geometry_test_image,
    create_real_world_geometry_test_image,
    create_test_image,
    create_v5_cleanup_test_image,
)


SUPPORTED_EXTENSIONS = {".png", ".jpg", ".jpeg"}
PRESETS = ("default", "logo_clean", "detail_preserving")
DEFAULT_REVIEW_DIR = Path(tempfile.gettempdir()) / "shai_quality_matrix"


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    project_root = Path.cwd()
    config = load_config(project_root / "config" / "config.yaml")
    review_dir = Path(args.review_dir).resolve()
    _reset_directory(review_dir)

    logger = _build_logger()
    logger.info("Quality matrix review folder: %s", review_dir)

    inputs = _discover_inputs(config, review_dir, include_samples=not args.no_samples)
    if not inputs:
        logger.error("No supported images found in %s", config.input_dir)
        return 1

    results: list[dict[str, Any]] = []
    for image_record in inputs:
        for preset in PRESETS:
            results.append(_run_one_job(config, image_record, preset, args.backend, review_dir, logger))

    _write_summary_files(results, review_dir)
    _create_contact_sheets(results, review_dir)
    _write_recommendation_report(results, review_dir)
    _print_console_summary(results, review_dir)
    return 0 if all(not result["failures"] for result in results) else 2


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run a Spool House Studio cleanup preset quality matrix.")
    parser.add_argument(
        "--review-dir",
        default=str(DEFAULT_REVIEW_DIR),
        help="Temp/review output folder. Defaults to the system temp shai_quality_matrix folder.",
    )
    parser.add_argument(
        "--backend",
        default="auto_vector_first",
        help="STL backend to request for every job. Defaults to auto_vector_first.",
    )
    parser.add_argument(
        "--no-samples",
        action="store_true",
        help="Only process images already present in input/.",
    )
    return parser.parse_args(argv)


def _reset_directory(path: Path) -> None:
    if path.exists():
        shutil.rmtree(path)
    path.mkdir(parents=True, exist_ok=True)


def _build_logger() -> logging.Logger:
    logger = logging.getLogger("spool_house_ai.quality_matrix")
    logger.handlers.clear()
    logger.setLevel(logging.INFO)
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("%(asctime)s | %(levelname)s | %(message)s", "%H:%M:%S"))
    logger.addHandler(handler)
    return logger


def _discover_inputs(config: AppConfig, review_dir: Path, include_samples: bool) -> list[dict[str, str]]:
    records: list[dict[str, str]] = []
    seen_paths: set[Path] = set()
    for path in sorted(config.input_dir.iterdir()):
        if path.is_file() and path.suffix.lower() in SUPPORTED_EXTENSIONS:
            resolved = path.resolve()
            seen_paths.add(resolved)
            records.append({"path": str(resolved), "source_type": "input", "label": path.stem})

    if include_samples:
        sample_input_dir = review_dir / "sample_inputs"
        sample_output_dir = review_dir / "_sample_generation_output"
        sample_config = replace(config, input_dir=sample_input_dir, output_dir=sample_output_dir)
        sample_input_dir.mkdir(parents=True, exist_ok=True)
        sample_creators = (
            create_test_image,
            create_geometry_test_image,
            create_real_world_geometry_test_image,
            create_v5_cleanup_test_image,
        )
        for creator in sample_creators:
            sample_path = creator(sample_config).resolve()
            if sample_path in seen_paths:
                continue
            records.append({"path": str(sample_path), "source_type": "test/sample", "label": sample_path.stem})

    return records


def _run_one_job(
    base_config: AppConfig,
    image_record: dict[str, str],
    preset: str,
    backend: str,
    review_dir: Path,
    logger: logging.Logger,
) -> dict[str, Any]:
    image_path = Path(image_record["path"])
    source_type = image_record["source_type"]
    job_slug = _slug(f"{source_type}_{image_path.stem}_{preset}")
    preset_output_root = review_dir / "pipeline_outputs" / preset / _slug(f"{source_type}_{image_path.stem}")
    job_log_dir = review_dir / "logs"
    job_log_dir.mkdir(parents=True, exist_ok=True)

    silhouette = apply_cleanup_preset(replace(base_config.silhouette, cleanup_preset=preset))
    stl = replace(base_config.stl, stl_backend=backend)
    job_config = replace(base_config, output_dir=preset_output_root, log_dir=job_log_dir, silhouette=silhouette, stl=stl)
    pipeline = ImagePipeline(job_config, logger)

    logger.info("Running %s with preset=%s backend=%s", image_path.name, preset, backend)
    ok = False
    try:
        ok = pipeline.process(image_path)
    except Exception as error:
        logger.exception("Quality matrix job crashed for %s preset=%s", image_path, preset)
        return _failed_result(image_record, preset, backend, job_slug, error)

    output_dir = preset_output_root / image_path.stem
    job_status_path = output_dir / "job_status.json"
    mesh_report_path = output_dir / "mesh_report.json"
    job_status = _read_json(job_status_path)
    mesh_report = _read_json(mesh_report_path)
    artifact_summary = job_status.get("artifact_summary") or {}
    failures = list(job_status.get("failures") or [])
    warnings = list(job_status.get("warnings") or [])
    if not ok and not failures:
        failures.append("Pipeline returned false")

    score, visual_note = _score_result(job_status, mesh_report, artifact_summary)
    return {
        "input_filename": image_path.name,
        "input_path": str(image_path),
        "source_type": source_type,
        "preset": preset,
        "requested_backend": job_status.get("requested_backend", backend),
        "actual_backend": job_status.get("actual_backend", ""),
        "fallback_used": bool(job_status.get("fallback_used", False)),
        "fallback_reason": job_status.get("fallback_reason", ""),
        "watertight": mesh_report.get("watertight"),
        "face_count": mesh_report.get("face_count"),
        "bounds": mesh_report.get("bounding_box_mm", []),
        "elapsed_time": job_status.get("duration_seconds"),
        "svg_path": job_status.get("svg_path", str(output_dir / f"{image_path.stem}.svg")),
        "review_svg_path": job_status.get("review_svg_path", str(output_dir / f"{image_path.stem}_review.svg")),
        "stl_path": job_status.get("stl_path", str(output_dir / f"{image_path.stem}.stl")),
        "mesh_report_path": str(mesh_report_path),
        "job_status_path": str(job_status_path),
        "output_folder": str(output_dir),
        "artifact_summary": artifact_summary,
        "warnings": warnings,
        "failures": failures,
        "quality_score": score,
        "visual_usability_note": visual_note,
        "preview_svg_path": str(output_dir / f"{image_path.stem}_preview_svg.png"),
        "preview_stl_path": str(output_dir / f"{image_path.stem}_preview_stl.png"),
        "preview_final_path": str(output_dir / f"{image_path.stem}_preview.png"),
        "contact_sheet_key": job_slug,
    }


def _failed_result(
    image_record: dict[str, str],
    preset: str,
    backend: str,
    job_slug: str,
    error: Exception,
) -> dict[str, Any]:
    return {
        "input_filename": Path(image_record["path"]).name,
        "input_path": image_record["path"],
        "source_type": image_record["source_type"],
        "preset": preset,
        "requested_backend": backend,
        "actual_backend": "",
        "fallback_used": False,
        "fallback_reason": "",
        "watertight": None,
        "face_count": 0,
        "bounds": [],
        "elapsed_time": None,
        "svg_path": "",
        "review_svg_path": "",
        "stl_path": "",
        "mesh_report_path": "",
        "job_status_path": "",
        "output_folder": "",
        "artifact_summary": {},
        "warnings": [],
        "failures": [str(error)],
        "quality_score": 0,
        "visual_usability_note": "failed before outputs were created",
        "preview_svg_path": "",
        "preview_stl_path": "",
        "preview_final_path": "",
        "contact_sheet_key": job_slug,
    }


def _read_json(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _score_result(
    job_status: dict[str, Any],
    mesh_report: dict[str, Any],
    artifact_summary: dict[str, Any],
) -> tuple[int, str]:
    score = 100
    warnings = job_status.get("warnings") or []
    failures = job_status.get("failures") or []
    if failures:
        score -= 50
    score -= min(25, len(warnings) * 5)
    if not mesh_report.get("watertight", False):
        score -= 20
    if not mesh_report.get("face_count"):
        score -= 20
    if job_status.get("fallback_used"):
        score -= 5
    preserved = int(artifact_summary.get("preserved_island_count") or 0)
    removed = int(artifact_summary.get("removed_island_count") or 0)
    score -= min(20, preserved * 2)
    score = max(0, score)

    if failures:
        note = "not usable: job failed"
    elif preserved:
        note = f"review needed: {preserved} tiny islands preserved"
    elif warnings:
        note = "review warnings before slicer"
    elif removed:
        note = f"cleaner: removed {removed} tiny islands"
    else:
        note = "looks ready for slicer review"
    return score, note


def _write_summary_files(results: list[dict[str, Any]], review_dir: Path) -> None:
    summary_path = review_dir / "quality_matrix_summary.json"
    summary_path.write_text(json.dumps(results, indent=2) + "\n", encoding="utf-8")

    csv_path = review_dir / "quality_matrix_summary.csv"
    columns = [
        "input_filename",
        "source_type",
        "preset",
        "requested_backend",
        "actual_backend",
        "fallback_used",
        "watertight",
        "face_count",
        "elapsed_time",
        "quality_score",
        "visual_usability_note",
        "isolated_island_count",
        "removed_island_count",
        "preserved_island_count",
        "preserved_detail_count",
        "svg_path",
        "review_svg_path",
        "stl_path",
        "mesh_report_path",
        "job_status_path",
    ]
    with csv_path.open("w", newline="", encoding="utf-8") as csv_file:
        writer = csv.DictWriter(csv_file, fieldnames=columns)
        writer.writeheader()
        for result in results:
            artifact = result["artifact_summary"]
            row = {column: result.get(column, "") for column in columns}
            for key in (
                "isolated_island_count",
                "removed_island_count",
                "preserved_island_count",
                "preserved_detail_count",
            ):
                row[key] = artifact.get(key, "")
            writer.writerow(row)


def _write_recommendation_report(results: list[dict[str, Any]], review_dir: Path) -> None:
    grouped = _group_by_image(results)
    lines = [
        "# Spool House Studio Quality Matrix",
        "",
        f"Generated: {datetime.now(timezone.utc).isoformat()}",
        "",
        "Scores are a sorting aid, not a substitute for visual review.",
        "",
        "## Per-Image Recommendations",
        "",
    ]
    for image_name, image_results in sorted(grouped.items()):
        ranked = sorted(image_results, key=lambda result: result["quality_score"], reverse=True)
        best = ranked[0]
        worst = ranked[-1]
        lines.extend(
            [
                f"### {image_name}",
                "",
                f"- Recommended preset: `{best['preset']}`",
                f"- Best score: {best['quality_score']} ({best['visual_usability_note']})",
                f"- Worst preset: `{worst['preset']}` score {worst['quality_score']}",
                f"- Logo clean helped: {_preset_helped(image_results, 'logo_clean')}",
                f"- Detail preserving helped: {_preset_helped(image_results, 'detail_preserving')}",
                f"- Product-clean enough: {'yes' if best['quality_score'] >= 90 and not best['warnings'] and not best['failures'] else 'review first'}",
                "",
            ]
        )

    top = sorted(results, key=lambda result: result["quality_score"], reverse=True)[:5]
    bottom = sorted(results, key=lambda result: result["quality_score"])[:5]
    lines.extend(["## Top 5 Best Outputs", ""])
    for result in top:
        lines.append(f"- {result['input_filename']} / `{result['preset']}`: {result['quality_score']} - {result['visual_usability_note']}")
    lines.extend(["", "## Top 5 Needing Cleanup", ""])
    for result in bottom:
        lines.append(f"- {result['input_filename']} / `{result['preset']}`: {result['quality_score']} - {result['visual_usability_note']}")

    lines.extend(["", "## Next Recommended Fixes", ""])
    lines.extend(_recommended_fixes(results))
    (review_dir / "quality_matrix_summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def _preset_helped(image_results: list[dict[str, Any]], preset: str) -> str:
    default = next((result for result in image_results if result["preset"] == "default"), None)
    other = next((result for result in image_results if result["preset"] == preset), None)
    if not default or not other:
        return "unknown"
    if other["quality_score"] > default["quality_score"]:
        return "yes"
    if other["quality_score"] == default["quality_score"]:
        return "neutral"
    return "no"


def _recommended_fixes(results: list[dict[str, Any]]) -> list[str]:
    preserved_total = sum(int((result["artifact_summary"] or {}).get("preserved_island_count") or 0) for result in results)
    fallback_total = sum(1 for result in results if result.get("fallback_used"))
    mesh_warning_total = sum(len(result.get("warnings") or []) for result in results)
    fixes = []
    if preserved_total:
        fixes.append("- Add richer visual review for preserved islands so intentional dots can be approved or removed faster.")
    if fallback_total:
        fixes.append("- Inspect vector fallback cases and decide whether they need vector repair or should remain raster-only.")
    if mesh_warning_total:
        fixes.append("- Group warning types in the GUI/report so artifact warnings and mesh warnings are easier to triage.")
    fixes.append("- Add a small curated regression image set to version control once representative artwork is approved.")
    return fixes[:3]


def _create_contact_sheets(results: list[dict[str, Any]], review_dir: Path) -> None:
    sheets_dir = review_dir / "contact_sheets"
    sheets_dir.mkdir(parents=True, exist_ok=True)
    grouped = _group_by_image(results)
    image_sheet_paths: list[Path] = []
    for image_name, image_results in grouped.items():
        sheet_path = sheets_dir / f"{_slug(image_name)}_preset_contact.png"
        _make_sheet(image_results, sheet_path, title=image_name)
        image_sheet_paths.append(sheet_path)

    ranked = sorted(results, key=lambda result: result["quality_score"], reverse=True)
    selected = ranked[:5] + ranked[-5:]
    _make_sheet(selected, review_dir / "quality_matrix_overall_best_worst.png", title="Best and worst quality matrix outputs")


def _make_sheet(results: list[dict[str, Any]], output_path: Path, title: str) -> None:
    tile_width = 420
    tile_height = 330
    title_height = 42
    cols = min(3, max(1, len(results)))
    rows = (len(results) + cols - 1) // cols
    sheet = Image.new("RGB", (cols * tile_width, title_height + rows * tile_height), (12, 14, 20))
    draw = ImageDraw.Draw(sheet)
    draw.text((16, 12), title, fill=(192, 132, 252), font=_font(24))

    for index, result in enumerate(results):
        x = (index % cols) * tile_width
        y = title_height + (index // cols) * tile_height
        tile = _make_tile(result, tile_width, tile_height)
        sheet.paste(tile, (x, y))
    sheet.save(output_path)


def _make_tile(result: dict[str, Any], width: int, height: int) -> Image.Image:
    tile = Image.new("RGB", (width, height), (18, 21, 29))
    draw = ImageDraw.Draw(tile)
    preview_path = _first_existing_path(
        result.get("preview_stl_path", ""),
        result.get("preview_svg_path", ""),
        result.get("preview_final_path", ""),
    )
    if preview_path:
        preview = Image.open(preview_path).convert("RGB")
        preview.thumbnail((width - 30, height - 110))
        tile.paste(preview, ((width - preview.width) // 2, 12))
    else:
        draw.rectangle((20, 20, width - 20, height - 115), outline=(80, 88, 108))
        draw.text((34, 70), "preview unavailable", fill=(190, 198, 214), font=_font(18))

    artifact = result.get("artifact_summary") or {}
    text_lines = [
        f"{result['preset']} | score {result['quality_score']}",
        f"backend {result.get('actual_backend') or result.get('requested_backend')}",
        f"islands kept {artifact.get('preserved_island_count', 0)} removed {artifact.get('removed_island_count', 0)}",
        result["visual_usability_note"],
    ]
    text_y = height - 92
    for line in text_lines:
        draw.text((12, text_y), _truncate(line, 52), fill=(230, 232, 238), font=_font(15))
        text_y += 20
    return tile


def _first_existing_path(*values: str) -> Path | None:
    for value in values:
        if value:
            path = Path(value)
            if path.exists():
                return path
    return None


def _font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    for font_path in (
        Path("C:/Windows/Fonts/segoeui.ttf"),
        Path("C:/Windows/Fonts/arial.ttf"),
    ):
        if font_path.exists():
            return ImageFont.truetype(str(font_path), size=size)
    return ImageFont.load_default()


def _group_by_image(results: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for result in results:
        key = f"{result['source_type']} / {result['input_filename']}"
        grouped.setdefault(key, []).append(result)
    return grouped


def _print_console_summary(results: list[dict[str, Any]], review_dir: Path) -> None:
    total = len(results)
    failed = sum(1 for result in results if result["failures"])
    passed = total - failed
    preset_scores: dict[str, list[int]] = {}
    for result in results:
        preset_scores.setdefault(result["preset"], []).append(int(result["quality_score"]))
    best_preset = max(
        preset_scores,
        key=lambda preset: sum(preset_scores[preset]) / max(1, len(preset_scores[preset])),
    )
    print(f"Quality matrix complete: {passed}/{total} jobs without failures")
    print(f"Best average preset: {best_preset}")
    print(f"Summary JSON: {review_dir / 'quality_matrix_summary.json'}")
    print(f"Summary CSV: {review_dir / 'quality_matrix_summary.csv'}")
    print(f"Summary MD: {review_dir / 'quality_matrix_summary.md'}")
    print(f"Contact sheets: {review_dir / 'contact_sheets'}")
    print(f"Overall sheet: {review_dir / 'quality_matrix_overall_best_worst.png'}")


def _slug(value: str) -> str:
    cleaned = []
    for char in value.lower():
        if char.isalnum():
            cleaned.append(char)
        elif char in {" ", "_", "-", ".", "/"}:
            cleaned.append("_")
    slug = "".join(cleaned).strip("_")
    while "__" in slug:
        slug = slug.replace("__", "_")
    return slug or "item"


def _truncate(value: str, limit: int) -> str:
    return value if len(value) <= limit else value[: limit - 3] + "..."


if __name__ == "__main__":
    sys.exit(main())
