#!/usr/bin/env python3

# Contest Management System - http://cms-dev.github.io/
# Copyright © 2024 CMS development group
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as
# published by the Free Software Foundation, either version 3 of the
# License, or (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU Affero General Public License for more details.
#
# You should have received a copy of the GNU Affero General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.

"""Utilities for processing user profile pictures.

This module provides functions for validating and processing user profile
pictures with security measures including size limits, MIME type validation,
and dimension constraints.
"""

import io
import logging
from typing import Tuple

from PIL import Image


logger = logging.getLogger(__name__)


# Maximum file size in bytes (5MB)
MAX_FILE_SIZE = 5 * 1024 * 1024

# Maximum dimensions in pixels
MAX_DIMENSION = 1280

# Minimum and maximum aspect ratios (width/height)
# Allows ratios from 1:4 (portrait) to 4:1 (landscape)
MIN_ASPECT_RATIO = 0.25
MAX_ASPECT_RATIO = 4.0

# Allowed MIME types and their corresponding PIL formats
ALLOWED_MIME_TYPES = {
    'image/jpeg': 'JPEG',
    'image/png': 'PNG',
    'image/gif': 'GIF',
    'image/webp': 'WEBP',
}

# PIL format to MIME type mapping
FORMAT_TO_MIME = {
    'JPEG': 'image/jpeg',
    'PNG': 'image/png',
    'GIF': 'image/gif',
    'WEBP': 'image/webp',
}


class PictureValidationError(Exception):
    """Exception raised when picture validation fails."""

    def __init__(self, code: str, message: str):
        self.code = code
        self.message = message
        super().__init__(message)


def validate_mime_type(content_type: str | None) -> str:
    """Validate that the MIME type is allowed.

    content_type: the MIME type of the uploaded file.

    return: the validated MIME type.

    raise (PictureValidationError): if the MIME type is not allowed.
    """
    if content_type is None or content_type not in ALLOWED_MIME_TYPES:
        raise PictureValidationError(
            "invalid_mime_type",
            "Invalid image type. Allowed types: JPEG, PNG, GIF, WEBP."
        )
    return content_type


def validate_file_size(data: bytes) -> None:
    """Validate that the file size is within limits.

    data: the file content as bytes.

    raise (PictureValidationError): if the file is too large.
    """
    if len(data) > MAX_FILE_SIZE:
        raise PictureValidationError(
            "file_too_large",
            f"Image file is too large. Maximum size is {MAX_FILE_SIZE // (1024 * 1024)}MB."
        )


def validate_and_get_image(data: bytes) -> Image.Image:
    """Validate image data and return a PIL Image object.

    This function validates that the data represents a valid image by
    attempting to open and verify it with PIL.

    data: the file content as bytes.

    return: a PIL Image object.

    raise (PictureValidationError): if the image is invalid.
    """
    try:
        img = Image.open(io.BytesIO(data))
        img.verify()
        # Re-open after verify (verify() can only be called once)
        img = Image.open(io.BytesIO(data))
    except Exception as e:
        logger.warning("Failed to open image: %s", e)
        raise PictureValidationError(
            "invalid_image",
            "The uploaded file is not a valid image."
        ) from e
    else:
        return img


def validate_dimensions(img: Image.Image) -> None:
    """Validate that the image dimensions are within limits.

    img: the PIL Image object.

    raise (PictureValidationError): if the dimensions exceed the maximum.
    """
    width, height = img.size
    if width > MAX_DIMENSION or height > MAX_DIMENSION:
        raise PictureValidationError(
            "dimensions_too_large",
            f"Image dimensions are too large. Maximum is {MAX_DIMENSION}x{MAX_DIMENSION} pixels."
        )


def validate_aspect_ratio(img: Image.Image) -> None:
    """Validate that the image aspect ratio is within reasonable limits.

    img: the PIL Image object.

    raise (PictureValidationError): if the aspect ratio is too extreme.
    """
    width, height = img.size
    if height == 0:
        raise PictureValidationError(
            "invalid_aspect_ratio",
            "Image has invalid dimensions."
        )
    aspect_ratio = width / height
    if aspect_ratio < MIN_ASPECT_RATIO or aspect_ratio > MAX_ASPECT_RATIO:
        raise PictureValidationError(
            "invalid_aspect_ratio",
            f"Image aspect ratio is too extreme. Allowed range is 1:{int(1/MIN_ASPECT_RATIO)} to {int(MAX_ASPECT_RATIO)}:1."
        )


def process_picture_upload(
    request_files: dict,
    file_cacher,
    description: str
) -> str | None:
    """Process a picture upload from a request and store it in the file cacher.

    This is a convenience function that handles the common pattern of:
    1. Checking if a picture file was uploaded
    2. Processing and validating the picture
    3. Storing it in the file cacher

    request_files: the request.files dictionary from Tornado.
    file_cacher: the FileCacher instance to store the picture.
    description: a description for the stored file.

    return: the digest of the stored picture, or None if no picture was uploaded.

    raise (PictureValidationError): if picture validation fails.
    """
    if "picture" not in request_files:
        return None

    picture_files = request_files["picture"]
    if not picture_files:
        return None

    picture_file = picture_files[0]
    if not picture_file.get("body"):
        return None

    processed_data, _ = process_picture(
        picture_file["body"],
        picture_file.get("content_type")
    )
    return file_cacher.put_file_content(processed_data, description)


def process_picture(
    data: bytes,
    content_type: str | None
) -> Tuple[bytes, str]:
    """Process and validate a user profile picture.

    This function performs the following steps:
    1. Validates the MIME type
    2. Validates the file size
    3. Validates that the data is a valid image
    4. Verifies declared MIME type matches actual image format
    5. Validates the dimensions
    6. Validates the aspect ratio
    7. Returns the validated image as bytes (preserving original dimensions)

    data: the file content as bytes.
    content_type: the MIME type of the uploaded file.

    return: a tuple of (processed image bytes, output MIME type).

    raise (PictureValidationError): if validation fails.
    """
    # Step 1: Validate MIME type
    validate_mime_type(content_type)

    # Step 2: Validate file size
    validate_file_size(data)

    # Step 3: Validate and open the image
    img = validate_and_get_image(data)

    # Step 4: Verify declared MIME type matches actual image format
    # This validation catches attempts to bypass MIME type restrictions by
    # renaming files or sending incorrect Content-Type headers. While PIL
    # would still process the image correctly, this check ensures users
    # cannot upload disallowed formats by simply changing the extension.
    expected_format = ALLOWED_MIME_TYPES.get(content_type)
    if expected_format and img.format != expected_format:
        raise PictureValidationError(
            "mime_type_mismatch",
            "The file content does not match the declared MIME type."
        )

    # Step 5: Validate dimensions
    validate_dimensions(img)

    # Step 6: Validate aspect ratio
    validate_aspect_ratio(img)

    # Step 7: Save the validated image (preserving original dimensions)
    output = io.BytesIO()

    # Determine output format based on input format
    # We preserve the original format instead of converting everything to JPEG
    # because: (1) PNG/GIF/WEBP support transparency which JPEG doesn't,
    # (2) PNG is better for images with sharp edges/text, and (3) users
    # may have intentionally chosen a specific format for quality reasons.
    img_format = img.format or 'PNG'
    if img_format not in FORMAT_TO_MIME:
        img_format = 'PNG'

    # Handle RGBA mode for JPEG (which doesn't support transparency)
    if img_format == 'JPEG' and img.mode == 'RGBA':
        img = img.convert('RGB')

    save_kwargs = {'format': img_format}
    if img_format == 'JPEG':
        save_kwargs['quality'] = 85
    img.save(output, **save_kwargs)
    output.seek(0)

    return output.read(), FORMAT_TO_MIME[img_format]
