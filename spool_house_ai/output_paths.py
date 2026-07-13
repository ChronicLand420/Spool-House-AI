from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class JobOutputPaths:
    output_root: Path
    job_root: Path
    source_dir: Path
    svg_dir: Path
    stl_dir: Path
    three_mf_dir: Path
    previews_dir: Path
    reports_dir: Path
    source_copy_path: Path
    cleaned_png_path: Path
    silhouette_png_path: Path
    body_mask_path: Path
    hole_mask_path: Path
    detail_mask_path: Path
    contour_debug_path: Path
    settings_path: Path
    svg_path: Path
    review_svg_path: Path
    stl_path: Path
    generic_3mf_path: Path
    mesh_report_path: Path
    job_status_path: Path
    job_summary_path: Path
    filament_swap_report_path: Path
    color_plan_path: Path
    filament_swap_plan_path: Path
    geometry_report_path: Path
    preview_path: Path

    def create_directories(self) -> None:
        for folder in (
            self.job_root,
            self.source_dir,
            self.svg_dir,
            self.stl_dir,
            self.three_mf_dir,
            self.previews_dir,
            self.reports_dir,
        ):
            folder.mkdir(parents=True, exist_ok=True)


def build_job_output_paths(output_root: Path, image_path: Path) -> JobOutputPaths:
    output_root = output_root.resolve()
    image_path = image_path.resolve()
    return build_job_output_paths_for_stem(output_root, image_path.stem, image_path.name)


def build_job_output_paths_for_stem(output_root: Path, stem: str, source_filename: str | None = None) -> JobOutputPaths:
    output_root = output_root.resolve()
    source_name = source_filename or f"{stem}"
    job_root = output_root / stem
    source_dir = job_root / "source"
    svg_dir = job_root / "svg"
    stl_dir = job_root / "stl"
    three_mf_dir = job_root / "3mf"
    previews_dir = job_root / "previews"
    reports_dir = job_root / "reports"

    return JobOutputPaths(
        output_root=output_root,
        job_root=job_root,
        source_dir=source_dir,
        svg_dir=svg_dir,
        stl_dir=stl_dir,
        three_mf_dir=three_mf_dir,
        previews_dir=previews_dir,
        reports_dir=reports_dir,
        source_copy_path=source_dir / source_name,
        cleaned_png_path=previews_dir / f"{stem}_cleaned.png",
        silhouette_png_path=previews_dir / f"{stem}_silhouette.png",
        body_mask_path=previews_dir / f"{stem}_body_mask.png",
        hole_mask_path=previews_dir / f"{stem}_hole_mask.png",
        detail_mask_path=previews_dir / f"{stem}_detail_mask.png",
        contour_debug_path=previews_dir / f"{stem}_contour_debug.png",
        settings_path=reports_dir / "job_settings.yaml",
        svg_path=svg_dir / f"{stem}.svg",
        review_svg_path=svg_dir / f"{stem}_review.svg",
        stl_path=stl_dir / f"{stem}.stl",
        generic_3mf_path=three_mf_dir / f"{stem}.3mf",
        mesh_report_path=reports_dir / "mesh_report.json",
        job_status_path=reports_dir / "job_status.json",
        job_summary_path=reports_dir / "job_summary.md",
        filament_swap_report_path=reports_dir / "filament_swap_report.json",
        color_plan_path=reports_dir / "color_plan.json",
        filament_swap_plan_path=reports_dir / "filament_swap_plan.txt",
        geometry_report_path=reports_dir / "geometry_report.txt",
        preview_path=previews_dir / f"{stem}_preview.png",
    )


def legacy_job_file(job_root: Path, stem: str, filename: str) -> Path:
    return job_root / filename.replace("{stem}", stem)
