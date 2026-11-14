"""
Gantt Chart Export Service

Provides export functionality for Gantt charts including:
- CSV export with work package data
- PNG export using HTML canvas
- JPG export with high quality
- PDF export for documentation
- Excel export with formatting

Complies with:
- Enterprise export standards
- High-quality output requirements
- Multiple format support
"""

import csv
import io
import logging
from datetime import datetime
from typing import Any, Dict, List, Optional


try:
    from PIL import Image, ImageDraw, ImageFont

    PIL_AVAILABLE = True
except ImportError:
    PIL_AVAILABLE = False
    logging.warning("PIL/Pillow not available - image export functionality limited")

try:
    import matplotlib.dates as mdates
    import matplotlib.pyplot as plt
    from matplotlib.patches import Rectangle  # noqa: F401 — availability probe: the import IS the test

    MATPLOTLIB_AVAILABLE = True
except ImportError:
    MATPLOTLIB_AVAILABLE = False
    logging.warning("Matplotlib not available - advanced chart export functionality limited")

try:
    import pandas as pd

    PANDAS_AVAILABLE = True
except ImportError:
    PANDAS_AVAILABLE = False
    logging.warning("Pandas not available - Excel export functionality limited")

logger = logging.getLogger(__name__)


class GanttExportService:
    """
    Service for exporting Gantt charts in various formats.
    """

    def __init__(self):
        self.supported_formats = ["csv", "png", "jpg", "pdf"]
        self.default_colors = {
            "planned": "#3b82f6",  # Blue
            "in_progress": "#eab308",  # Yellow
            "completed": "#22c55e",  # Green
            "cancelled": "#6b7280",  # Gray
            "critical": "#ef4444",  # Red
            "high": "#f97316",  # Orange
            "medium": "#8b5cf6",  # Purple
            "low": "#06b6d4",  # Cyan
        }

    def export_to_csv(
        self, work_packages: List[Dict[str, Any]], filename: Optional[str] = None
    ) -> str:
        """
        Export work packages to CSV format.

        Args:
            work_packages: List of work package dictionaries
            filename: Optional filename for the export

        Returns:
            CSV content as string
        """
        try:
            if not filename:
                filename = f"gantt_chart_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"

            # Define CSV headers
            headers = [
                "ID",
                "Name",
                "Description",
                "Status",
                "Priority",
                "Assigned To",
                "Start Date",
                "End Date",
                "Duration (Days)",
                "Progress (%)",
                "Estimated Cost",
                "Actual Cost",
                "Risk Level",
                "Dependencies",
            ]

            # Create CSV content
            output = io.StringIO()
            writer = csv.writer(output)

            # Write headers
            writer.writerow(headers)

            # Write work package data
            for wp in work_packages:
                row = [
                    wp.get("id", ""),
                    wp.get("name", ""),
                    wp.get("description", ""),
                    wp.get("status", ""),
                    wp.get("priority", ""),
                    wp.get("assigned_to", ""),
                    wp.get("start_date", ""),
                    wp.get("end_date", ""),
                    wp.get("duration_days", ""),
                    wp.get("progress_percentage", ""),
                    wp.get("estimated_cost", ""),
                    wp.get("actual_cost", ""),
                    wp.get("risk_level", ""),
                    str(wp.get("dependencies", [])),
                ]
                writer.writerow(row)

            csv_content = output.getvalue()
            output.close()

            logger.info(f"CSV export completed: {len(work_packages)} work packages")
            return csv_content

        except Exception as e:
            logger.error(f"Error exporting to CSV: {e}")
            raise

    def export_to_excel(
        self, work_packages: List[Dict[str, Any]], filename: Optional[str] = None
    ) -> bytes:
        """
        Export work packages to Excel format with formatting.

        Args:
            work_packages: List of work package dictionaries
            filename: Optional filename for the export

        Returns:
            Excel file content as bytes
        """
        if not PANDAS_AVAILABLE:
            raise ImportError("Pandas is required for Excel export")

        try:
            if not filename:
                filename = f"gantt_chart_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx"

            # Create DataFrame
            data = []
            for wp in work_packages:
                data.append(
                    {
                        "ID": wp.get("id", ""),
                        "Name": wp.get("name", ""),
                        "Description": wp.get("description", ""),
                        "Status": wp.get("status", ""),
                        "Priority": wp.get("priority", ""),
                        "Assigned To": wp.get("assigned_to", ""),
                        "Start Date": wp.get("start_date", ""),
                        "End Date": wp.get("end_date", ""),
                        "Duration (Days)": wp.get("duration_days", ""),
                        "Progress (%)": wp.get("progress_percentage", ""),
                        "Estimated Cost": wp.get("estimated_cost", ""),
                        "Actual Cost": wp.get("actual_cost", ""),
                        "Risk Level": wp.get("risk_level", ""),
                        "Dependencies": str(wp.get("dependencies", [])),
                    }
                )

            df = pd.DataFrame(data)

            # Create Excel writer with formatting
            output = io.BytesIO()
            with pd.ExcelWriter(output, engine="openpyxl") as writer:
                df.to_excel(writer, sheet_name="Work Packages", index=False)

                # Get the workbook and worksheet for formatting
                worksheet = writer.sheets["Work Packages"]

                # Auto-adjust column widths
                for column in worksheet.columns:
                    max_length = 0
                    column_letter = column[0].column_letter
                    for cell in column:
                        try:
                            if len(str(cell.value)) > max_length:
                                max_length = len(str(cell.value))
                        except Exception as e:
                            logger.debug("Failed to calculate cell width: %s", e)
                    adjusted_width = min(max_length + 2, 50)
                    worksheet.column_dimensions[column_letter].width = adjusted_width

                # Add conditional formatting for status
                from openpyxl.formatting.rule import CellIsRule
                from openpyxl.styles import PatternFill

                # Status colors
                status_colors = {
                    "completed": "00FF00",  # Green
                    "in_progress": "FFFF00",  # Yellow
                    "planned": "00B0F0",  # Blue
                    "cancelled": "808080",  # Gray
                }

                for status, color in status_colors.items():
                    fill = PatternFill(start_color=color, end_color=color, fill_type="solid")
                    rule = CellIsRule(operator="equal", formula=[f'"{status}"'], fill=fill)
                    worksheet.conditional_formatting.add(f"D2:D{len(work_packages)+1}", rule)

            excel_content = output.getvalue()
            output.close()

            logger.info(f"Excel export completed: {len(work_packages)} work packages")
            return excel_content

        except Exception as e:
            logger.error(f"Error exporting to Excel: {e}")
            raise

    def export_to_png(
        self,
        work_packages: List[Dict[str, Any]],
        width: int = 1200,
        height: int = 600,
        filename: Optional[str] = None,
    ) -> bytes:
        """
        Export Gantt chart to PNG format using matplotlib.

        Args:
            work_packages: List of work package dictionaries
            width: Image width in pixels
            height: Image height in pixels
            filename: Optional filename for the export

        Returns:
            PNG image content as bytes
        """
        if not MATPLOTLIB_AVAILABLE:
            # Fallback to PIL-based simple chart
            return self._export_to_png_simple(work_packages, width, height, filename)

        try:
            if not filename:
                filename = f"gantt_chart_{datetime.now().strftime('%Y%m%d_%H%M%S')}.png"

            # Create figure and axis
            fig, ax = plt.subplots(figsize=(width / 100, height / 100), dpi=100)

            # Filter work packages with dates
            valid_packages = [
                wp for wp in work_packages if wp.get("start_date") and wp.get("end_date")
            ]

            if not valid_packages:
                # Create empty chart with message
                ax.text(
                    0.5,
                    0.5,
                    "No work packages with valid dates",
                    horizontalalignment="center",
                    verticalalignment="center",
                    transform=ax.transAxes,
                    fontsize=16,
                )
            else:
                # Convert dates to matplotlib format
                for wp in valid_packages:
                    wp["start_date_obj"] = datetime.strptime(wp["start_date"], "%Y-%m-%d")
                    wp["end_date_obj"] = datetime.strptime(wp["end_date"], "%Y-%m-%d")

                # Sort by start date
                valid_packages.sort(key=lambda x: x["start_date_obj"])

                # Create Gantt chart
                range(len(valid_packages))

                for i, wp in enumerate(valid_packages):
                    # Get color based on status
                    color = self.default_colors.get(wp.get("status", "planned"), "#3b82f6")

                    # Draw bar
                    start = mdates.date2num(wp["start_date_obj"])
                    end = mdates.date2num(wp["end_date_obj"])
                    duration = end - start

                    ax.barh(i, duration, left=start, height=0.8, color=color, alpha=0.8)

                    # Add progress bar if available
                    if (wp.get("progress_percentage") or 0) > 0:
                        progress_duration = duration * (wp["progress_percentage"] / 100)
                        ax.barh(
                            i,
                            progress_duration,
                            left=start,
                            height=0.8,
                            color="darkgreen",
                            alpha=0.6,
                        )

                    # Add work package name
                    ax.text(
                        start + duration / 2,
                        i,
                        wp["name"][:30],
                        ha="center",
                        va="center",
                        fontsize=8,
                        fontweight="bold",
                    )

            # Formatting
            ax.set_yticks(range(len(valid_packages)))
            ax.set_yticklabels([wp["name"][:25] for wp in valid_packages])
            ax.set_xlabel("Timeline")
            ax.set_title("Implementation Gantt Chart", fontsize=16, fontweight="bold")

            # Format x-axis as dates
            ax.xaxis.set_major_formatter(mdates.DateFormatter("%Y-%m-%d"))
            ax.xaxis.set_major_locator(mdates.MonthLocator())
            plt.xticks(rotation=45)

            # Add grid
            ax.grid(True, alpha=0.3)

            # Adjust layout
            plt.tight_layout()

            # Save to bytes
            output = io.BytesIO()
            plt.savefig(output, format="png", dpi=150, bbox_inches="tight")
            output.seek(0)
            plt.close()

            png_content = output.getvalue()
            output.close()

            logger.info(f"PNG export completed: {len(valid_packages)} work packages")
            return png_content

        except Exception as e:
            logger.error(f"Error exporting to PNG: {e}")
            # Fallback to simple version
            return self._export_to_png_simple(work_packages, width, height, filename)

    def _export_to_png_simple(
        self,
        work_packages: List[Dict[str, Any]],
        width: int,
        height: int,
        filename: Optional[str] = None,
    ) -> bytes:
        """
        Simple PNG export using PIL when matplotlib is not available.
        """
        if not PIL_AVAILABLE:
            raise ImportError("PIL/Pillow is required for PNG export")

        try:
            # Create image
            img = Image.new("RGB", (width, height), color="white")
            draw = ImageDraw.Draw(img)

            # Try to load a font
            try:
                title_font = ImageFont.truetype("arial.ttf", 20)
                text_font = ImageFont.truetype("arial.ttf", 12)
            except (OSError, IOError):
                title_font = ImageFont.load_default()
                text_font = ImageFont.load_default()

            # Draw title
            draw.text(
                (width // 2 - 100, 20), "Implementation Gantt Chart", fill="black", font=title_font
            )

            # Draw simple representation
            y_offset = 80
            for i, wp in enumerate(work_packages[:10]):  # Limit to 10 items
                # Draw work package name
                draw.text((20, y_offset), wp.get("name", "Unknown"), fill="black", font=text_font)

                # Draw status bar
                color = self.default_colors.get(wp.get("status", "planned"), "#3b82f6")
                draw.rectangle(
                    [
                        (200, y_offset),
                        (200 + int((wp.get("progress_percentage") or 0) * 2), y_offset + 20),
                    ],
                    fill=color,
                )

                y_offset += 30

            # Save to bytes
            output = io.BytesIO()
            img.save(output, format="PNG")
            output.seek(0)

            png_content = output.getvalue()
            output.close()

            logger.info(f"Simple PNG export completed: {len(work_packages)} work packages")
            return png_content

        except Exception as e:
            logger.error(f"Error in simple PNG export: {e}")
            raise

    def export_to_jpg(
        self,
        work_packages: List[Dict[str, Any]],
        width: int = 1200,
        height: int = 600,
        quality: int = 95,
        filename: Optional[str] = None,
    ) -> bytes:
        """
        Export Gantt chart to JPG format.

        Args:
            work_packages: List of work package dictionaries
            width: Image width in pixels
            height: Image height in pixels
            quality: JPEG quality (1 - 100)
            filename: Optional filename for the export

        Returns:
            JPG image content as bytes
        """
        try:
            if not filename:
                filename = f"gantt_chart_{datetime.now().strftime('%Y%m%d_%H%M%S')}.jpg"

            # Generate PNG first
            png_content = self.export_to_png(work_packages, width, height, filename)

            # Convert PNG to JPG
            if PIL_AVAILABLE:
                # Open PNG and convert to JPG
                img = Image.open(io.BytesIO(png_content))

                # Convert to RGB if necessary (for transparency)
                if img.mode in ("RGBA", "LA", "P"):
                    # Create white background
                    background = Image.new("RGB", img.size, (255, 255, 255))
                    if img.mode == "P":
                        img = img.convert("RGBA")
                    background.paste(img, mask=img.split()[-1] if img.mode == "RGBA" else None)
                    img = background

                # Save as JPG
                output = io.BytesIO()
                img.save(output, format="JPEG", quality=quality)
                output.seek(0)

                jpg_content = output.getvalue()
                output.close()

                logger.info(f"JPG export completed: quality={quality}")
                return jpg_content
            else:
                # Fallback - return PNG content
                logger.warning("PIL not available, returning PNG content instead")
                return png_content

        except Exception as e:
            logger.error(f"Error exporting to JPG: {e}")
            raise

    def export_to_pdf(
        self, work_packages: List[Dict[str, Any]], filename: Optional[str] = None
    ) -> bytes:
        """
        Export Gantt chart to PDF format.

        Args:
            work_packages: List of work package dictionaries
            filename: Optional filename for the export

        Returns:
            PDF content as bytes
        """
        if not MATPLOTLIB_AVAILABLE:
            raise ImportError("Matplotlib is required for PDF export")

        try:
            if not filename:
                filename = f"gantt_chart_{datetime.now().strftime('%Y%m%d_%H%M%S')}.pdf"

            # Create figure and axis (PDF format)
            fig, ax = plt.subplots(figsize=(11, 8.5), dpi=100)

            # Filter work packages with dates
            valid_packages = [
                wp for wp in work_packages if wp.get("start_date") and wp.get("end_date")
            ]

            if not valid_packages:
                # Create empty chart with message
                ax.text(
                    0.5,
                    0.5,
                    "No work packages with valid dates",
                    horizontalalignment="center",
                    verticalalignment="center",
                    transform=ax.transAxes,
                    fontsize=16,
                )
            else:
                # Convert dates to matplotlib format
                for wp in valid_packages:
                    wp["start_date_obj"] = datetime.strptime(wp["start_date"], "%Y-%m-%d")
                    wp["end_date_obj"] = datetime.strptime(wp["end_date"], "%Y-%m-%d")

                # Sort by start date
                valid_packages.sort(key=lambda x: x["start_date_obj"])

                # Create Gantt chart
                range(len(valid_packages))

                for i, wp in enumerate(valid_packages):
                    # Get color based on status
                    color = self.default_colors.get(wp.get("status", "planned"), "#3b82f6")

                    # Draw bar
                    start = mdates.date2num(wp["start_date_obj"])
                    end = mdates.date2num(wp["end_date_obj"])
                    duration = end - start

                    ax.barh(i, duration, left=start, height=0.8, color=color, alpha=0.8)

                    # Add progress bar if available
                    if (wp.get("progress_percentage") or 0) > 0:
                        progress_duration = duration * (wp["progress_percentage"] / 100)
                        ax.barh(
                            i,
                            progress_duration,
                            left=start,
                            height=0.8,
                            color="darkgreen",
                            alpha=0.6,
                        )

                    # Add work package name
                    ax.text(
                        start + duration / 2,
                        i,
                        wp["name"][:30],
                        ha="center",
                        va="center",
                        fontsize=8,
                        fontweight="bold",
                    )

            # Formatting
            ax.set_yticks(range(len(valid_packages)))
            ax.set_yticklabels([wp["name"][:25] for wp in valid_packages])
            ax.set_xlabel("Timeline")
            ax.set_title("Implementation Gantt Chart", fontsize=16, fontweight="bold")

            # Format x-axis as dates
            ax.xaxis.set_major_formatter(mdates.DateFormatter("%Y-%m-%d"))
            ax.xaxis.set_major_locator(mdates.MonthLocator())
            plt.xticks(rotation=45)

            # Add grid
            ax.grid(True, alpha=0.3)

            # Adjust layout
            plt.tight_layout()

            # Save to bytes
            output = io.BytesIO()
            plt.savefig(output, format="pdf", bbox_inches="tight")
            output.seek(0)
            plt.close()

            pdf_content = output.getvalue()
            output.close()

            logger.info(f"PDF export completed: {len(valid_packages)} work packages")
            return pdf_content

        except Exception as e:
            logger.error(f"Error exporting to PDF: {e}")
            raise

    def get_export_summary(self, work_packages: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        Get summary statistics for export data.

        Args:
            work_packages: List of work package dictionaries

        Returns:
            Dictionary with export summary statistics
        """
        total_packages = len(work_packages)
        valid_packages = len(
            [wp for wp in work_packages if wp.get("start_date") and wp.get("end_date")]
        )

        status_counts = {}
        priority_counts = {}

        for wp in work_packages:
            # Count by status
            status = wp.get("status", "unknown")
            status_counts[status] = status_counts.get(status, 0) + 1

            # Count by priority
            priority = wp.get("priority", "unknown")
            priority_counts[priority] = priority_counts.get(priority, 0) + 1

        return {
            "total_work_packages": total_packages,
            "valid_packages": valid_packages,
            "exportable_packages": valid_packages,
            "status_distribution": status_counts,
            "priority_distribution": priority_counts,
            "supported_formats": self.supported_formats,
            "export_timestamp": datetime.now().isoformat(),
        }
