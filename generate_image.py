#!/usr/bin/env python3
"""
CLI script to generate Kindle display image and save to disk.
Run this via cron to keep the image updated.
"""

import argparse
import logging
import sys
from pathlib import Path

from app.main import generate_composite_image

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


def main():
    parser = argparse.ArgumentParser(description="Generate Kindle display image and save to disk")
    parser.add_argument(
        "output",
        type=Path,
        help="Output path for PNG file (e.g., /var/www/html/img/calendar.png)",
    )
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Enable verbose logging",
    )

    args = parser.parse_args()

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    try:
        logger.info("Generating image...")
        image_bytes = generate_composite_image()

        # Create parent directory if it doesn't exist
        args.output.parent.mkdir(parents=True, exist_ok=True)

        # Write image to file
        logger.info(f"Writing image to {args.output}")
        args.output.write_bytes(image_bytes)

        logger.info(f"Successfully generated image: {args.output} ({len(image_bytes)} bytes)")
        return 0

    except Exception as e:
        logger.error(f"Failed to generate image: {e}", exc_info=True)
        return 1


if __name__ == "__main__":
    sys.exit(main())
