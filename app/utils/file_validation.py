"""
File Upload Security Validation

Provides MIME type validation based on magic numbers (file headers)
to prevent malicious file uploads disguised with wrong extensions.

IMP-001: Add MIME type validation for file uploads
"""

import logging
import zipfile
from typing import Optional, Set

# Try to import python-magic (may not be installed in all environments)
try:
    import magic
    MAGIC_AVAILABLE = True
except ImportError:
    magic = None
    MAGIC_AVAILABLE = False

logger = logging.getLogger(__name__)

# Allowed MIME types for file uploads
ALLOWED_MIME_TYPES = {
    'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',  # .xlsx
    'application/vnd.ms-excel',  # .xls
    'text/csv',  # .csv
    'text/plain',  # .csv (alternative MIME type)
    'application/json',  # .json
    'application/octet-stream',  # Generic binary (fallback for some valid files)
}

# MIME types that should be explicitly rejected (known malicious)
REJECTED_MIME_TYPES = {
    'application/x-python-code',
    'application/x-sh',
    'application/x-executable',
    'application/x-msdownload',
    'text/x-python',
    'text/x-shellscript',
}


XLSX_MIME = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'


def is_xlsx_package(file) -> bool:
    """True when the stream is a zip holding the parts every .xlsx must have.

    Libraries that write spreadsheets differ in what a header sniffer reports
    (often plain ``application/zip``), so the package structure is the check.
    The stream position is restored afterwards.
    """
    try:
        file.seek(0)
        with zipfile.ZipFile(file) as archive:
            names = set(archive.namelist())
        return "[Content_Types].xml" in names and "xl/workbook.xml" in names
    except (zipfile.BadZipFile, OSError, ValueError):
        return False
    finally:
        file.seek(0)


class InvalidFileTypeError(ValueError):
    """Raised when uploaded file has invalid MIME type."""

    def __init__(self, detected_mime: str, filename: str = None):
        self.detected_mime = detected_mime
        self.filename = filename
        message = f"Invalid file type: {detected_mime}"
        if filename:
            message += f" (file: {filename})"
        super().__init__(message)


def validate_mime_type(file, filename: Optional[str] = None) -> str:
    """
    Validate file MIME type based on magic numbers (file header).

    Reads first 1024 bytes to detect MIME type, then resets file pointer.
    This prevents malicious files from bypassing validation by using
    misleading file extensions.

    Args:
        file: File-like object (werkzeug.datastructures.FileStorage or similar)
        filename: Optional filename for better error messages

    Returns:
        str: Detected MIME type

    Raises:
        InvalidFileTypeError: If MIME type is not in whitelist

    Examples:
        >>> from werkzeug.datastructures import FileStorage
        >>> file = FileStorage(stream=open('data.xlsx', 'rb'))
        >>> mime = validate_mime_type(file)
        >>> assert mime == 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
    """
    is_xlsx_name = bool(filename) and filename.lower().endswith(".xlsx")
    if is_xlsx_name:
        # Decide by the file's structure, not by what the sniffer calls it.
        if is_xlsx_package(file):
            return XLSX_MIME
        logger.warning("Rejected %s: not a valid .xlsx package", filename)
        raise InvalidFileTypeError("not an Excel workbook", filename)

    if not MAGIC_AVAILABLE:
        logger.warning(
            "python-magic not installed. MIME type validation disabled. "
            "Install with: pip install python-magic python-magic-bin"
        )
        # Fallback: allow upload but log warning
        return "application/octet-stream"

    try:
        # Read first 1024 bytes for MIME detection
        file_header = file.read(1024)
        file.seek(0)  # Reset file pointer for actual processing

        # Detect MIME type from magic numbers
        detected_mime = magic.from_buffer(file_header, mime=True)

        logger.debug(
            f"Detected MIME type: {detected_mime} for file: {filename or 'unknown'}"
        )

        # Check for explicitly rejected types (malicious files)
        if detected_mime in REJECTED_MIME_TYPES:
            logger.warning(
                f"Rejected malicious file type: {detected_mime} "
                f"(file: {filename or 'unknown'})"
            )
            raise InvalidFileTypeError(detected_mime, filename)

        # Check whitelist
        if detected_mime not in ALLOWED_MIME_TYPES:
            logger.warning(
                f"Rejected file type: {detected_mime} (file: {filename or 'unknown'}). "
                f"Allowed types: {', '.join(ALLOWED_MIME_TYPES)}"
            )
            raise InvalidFileTypeError(detected_mime, filename)

        return detected_mime

    except InvalidFileTypeError:
        # Re-raise validation errors
        raise
    except Exception as e:
        # Log unexpected errors but don't block upload
        logger.error(
            f"Error validating MIME type for {filename or 'unknown'}: {e}. "
            "Allowing upload (degraded security)."
        )
        return "application/octet-stream"


def get_allowed_mime_types() -> Set[str]:
    """
    Get set of allowed MIME types.

    Returns:
        Set[str]: Allowed MIME types
    """
    return ALLOWED_MIME_TYPES.copy()


def get_allowed_extensions_display() -> str:
    """
    Get human-readable string of allowed file types.

    Returns:
        str: Display string (e.g., "Excel (.xlsx, .xls), CSV, JSON")
    """
    return "Excel (.xlsx, .xls), CSV, JSON"
