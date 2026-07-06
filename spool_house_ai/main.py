from __future__ import annotations

import argparse
from dataclasses import replace
from pathlib import Path

from spool_house_ai.config import AppConfig, load_config
from spool_house_ai.logging_setup import configure_logging


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Spool House AI image-to-STL automation.")
    parser.add_argument(
        "--config",
        default="config/config.yaml",
        help="Path to the YAML config file.",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="Process existing images in the input folder once, then exit.",
    )
    parser.add_argument(
        "--watch",
        action="store_true",
        help="Watch the input folder for new images.",
    )
    parser.add_argument(
        "--product-mode",
        choices=["flat_relief", "keychain", "wall_art"],
        help="Override the configured product mode.",
    )
    parser.add_argument(
        "--threshold",
        type=int,
        help="Override the black/white threshold value.",
    )
    parser.add_argument(
        "--height",
        type=float,
        help="Override the extrusion height in millimeters.",
    )
    parser.add_argument(
        "--stl-backend",
        choices=["raster_heightfield", "vector_extrusion"],
        help="Override the STL backend. raster_heightfield remains the safe default.",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Enable more verbose debug logging.",
    )
    parser.add_argument(
        "--test",
        action="store_true",
        help="Create a built-in sample image, run the full pipeline, and verify outputs.",
    )
    return parser.parse_args()


def process_existing_images(config: AppConfig, pipeline: ImagePipeline) -> None:
    supported_extensions = {".png", ".jpg", ".jpeg"}
    for image_path in sorted(config.input_dir.iterdir()):
        if image_path.is_file() and image_path.suffix.lower() in supported_extensions:
            pipeline.process(image_path)


def main() -> None:
    args = parse_args()
    config = apply_cli_overrides(load_config(Path(args.config)), args)
    logger = configure_logging(config.log_dir)
    if config.pipeline.debug:
        logger.setLevel("DEBUG")

    try:
        from spool_house_ai.pipeline import ImagePipeline
        from spool_house_ai.test_mode import run_test_mode
        from spool_house_ai.watcher import watch_input_folder
    except ModuleNotFoundError as error:
        missing_name = error.name or "unknown"
        logger.error("Missing dependency: %s", missing_name)
        logger.error("Install dependencies with: pip install -r requirements.txt")
        raise SystemExit(1) from error

    pipeline = ImagePipeline(config=config, logger=logger)

    logger.info("Spool House AI started")
    logger.info("Input folder: %s", config.input_dir)
    logger.info("Output folder: %s", config.output_dir)

    if args.test:
        success = run_test_mode(config, pipeline, logger)
        if not success:
            raise SystemExit(1)
        logger.info("Test mode complete")
        return

    if args.once:
        process_existing_images(config, pipeline)
        logger.info("One-time processing complete")
        return

    watch_input_folder(config=config, pipeline=pipeline, logger=logger)


def apply_cli_overrides(config: AppConfig, args: argparse.Namespace) -> AppConfig:
    pipeline_config = config.pipeline
    silhouette_config = config.silhouette
    stl_config = config.stl

    if args.product_mode:
        pipeline_config = replace(pipeline_config, product_mode=args.product_mode)
        stl_config = replace(stl_config, product_mode=args.product_mode)
    else:
        stl_config = replace(stl_config, product_mode=pipeline_config.product_mode)

    if args.threshold is not None:
        silhouette_config = replace(silhouette_config, threshold_value=args.threshold)

    if args.height is not None:
        stl_config = replace(stl_config, extrusion_height_mm=args.height)

    if args.stl_backend:
        stl_config = replace(stl_config, stl_backend=args.stl_backend)

    if args.debug:
        pipeline_config = replace(pipeline_config, debug=True)

    return replace(
        config,
        pipeline=pipeline_config,
        silhouette=silhouette_config,
        stl=stl_config,
    )


if __name__ == "__main__":
    main()
