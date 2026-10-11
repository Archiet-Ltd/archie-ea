"""
TOGAF Deliverable Export Service (S0-02)

Provides PDF and Excel export for TOGAF-specific architecture deliverables:
- Architecture Vision Document (Phase A)
- Compliance Scan Report (Phase G)
- Vendor Selection Report
- Architecture Roadmap (Phase E/F)

Follows the ARBExportService pattern: conditional imports for reportlab/pandas,
branded header/footer, and graceful degradation when dependencies are missing.
"""

import io
from html import escape
import logging
from datetime import datetime

try:
    import pandas as pd

    PANDAS_AVAILABLE = True
except ImportError:
    PANDAS_AVAILABLE = False

try:
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import letter
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import inch
    from reportlab.platypus import (
        Paragraph,
        SimpleDocTemplate,
        Spacer,
        Table,
        TableStyle,
    )

    REPORTLAB_AVAILABLE = True
except ImportError:
    REPORTLAB_AVAILABLE = False

logger = logging.getLogger(__name__)

# Branding constants
BRAND_COLOR = colors.HexColor("#1e3a5f") if REPORTLAB_AVAILABLE else None
ACCENT_COLOR = colors.HexColor("#3b82f6") if REPORTLAB_AVAILABLE else None
CONFIDENTIALITY_NOTICE = "CONFIDENTIAL — For authorized recipients only"


class TOGAFDeliverableExportService:
    """Export service for TOGAF architecture deliverables."""

    def __init__(self):
        self.supported_formats = ["csv", "excel", "pdf"]

    # =========================================================================
    # PDF HELPERS
    # =========================================================================

    def _get_branded_styles(self):
        """Return branded paragraph styles for TOGAF deliverables."""
        styles = getSampleStyleSheet()

        def _add(style):
            try:
                styles.add(style)
            except KeyError:
                pass  # already registered in global stylesheet

        _add(ParagraphStyle(
            "BrandTitle",
            parent=styles["Heading1"],
            fontSize=20,
            textColor=BRAND_COLOR,
            spaceAfter=6,
        ))
        _add(ParagraphStyle(
            "BrandSubtitle",
            parent=styles["Normal"],
            fontSize=12,
            textColor=colors.grey,
            spaceAfter=20,
        ))
        _add(ParagraphStyle(
            "SectionHead",
            parent=styles["Heading2"],
            fontSize=14,
            textColor=BRAND_COLOR,
            spaceBefore=16,
            spaceAfter=8,
        ))
        _add(ParagraphStyle(
            "BodyText",
            parent=styles["Normal"],
            fontSize=10,
            leading=14,
            spaceAfter=8,
        ))
        return styles

    def _build_branded_header(self, elements, styles, title, phase_label, timestamp):
        """Add branded title page elements."""
        elements.append(Paragraph("Entelim", styles["BrandTitle"]))
        elements.append(
            Paragraph(
                f"Enterprise Architecture Platform — {phase_label}",
                styles["BrandSubtitle"],
            )
        )
        elements.append(Spacer(1, 12))
        elements.append(Paragraph(title, styles["Heading1"]))
        elements.append(
            Paragraph(
                f"Generated: {timestamp} | {CONFIDENTIALITY_NOTICE}",
                styles["Normal"],
            )
        )
        elements.append(Spacer(1, 24))

    def _build_branded_footer(self, elements, styles):
        """Add branded footer."""
        elements.append(Spacer(1, 30))
        elements.append(
            Paragraph(
                f"<i>{CONFIDENTIALITY_NOTICE} | Entelim Platform</i>",
                ParagraphStyle(
                    "Footer",
                    parent=styles["Normal"],
                    fontSize=8,
                    textColor=colors.grey,
                    alignment=1,
                ),
            )
        )

    def _make_table(self, data, col_widths=None, header_color=None):
        """Create a styled table following the ARB export pattern."""
        if not data:
            return None

        table = Table(data, colWidths=col_widths)
        bg = header_color or ACCENT_COLOR
        style_commands = [
            ("BACKGROUND", (0, 0), (-1, 0), bg),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTSIZE", (0, 0), (-1, 0), 10),
            ("BACKGROUND", (0, 1), (-1, -1), colors.white),
            ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
            ("FONTNAME", (0, 1), (-1, -1), "Helvetica"),
            ("FONTSIZE", (0, 1), (-1, -1), 9),
            ("PADDING", (0, 0), (-1, -1), 5),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ]
        # Alternate row shading
        for i in range(1, len(data)):
            if i % 2 == 0:
                style_commands.append(
                    ("BACKGROUND", (0, i), (-1, i), colors.HexColor("#f8fafc"))
                )
        table.setStyle(TableStyle(style_commands))
        return table

    # =========================================================================
    # ARCHITECTURE VISION DOCUMENT (Phase A)
    # =========================================================================

    def export_vision_to_pdf(self, vision_doc) -> bytes:
        """Export an ArchitectureVisionDocument to branded PDF.

        Args:
            vision_doc: ArchitectureVisionDocument model instance

        Returns:
            PDF content as bytes
        """
        if not REPORTLAB_AVAILABLE:
            raise ImportError("ReportLab is required for PDF export")

        output = io.BytesIO()
        doc = SimpleDocTemplate(output, pagesize=letter)
        elements = []
        styles = self._get_branded_styles()
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")

        self._build_branded_header(
            elements, styles, vision_doc.title,
            "TOGAF ADM Phase A: Architecture Vision", timestamp,
        )

        # Scope
        if vision_doc.scope_summary:
            elements.append(Paragraph("Scope", styles["SectionHead"]))
            elements.append(Paragraph(vision_doc.scope_summary, styles["BodyText"]))

        # Stakeholders
        stakeholders = vision_doc.stakeholder_concerns or []
        if stakeholders:
            elements.append(Paragraph("Stakeholder Concerns", styles["SectionHead"]))
            for s in stakeholders[:20]:
                if isinstance(s, dict):
                    name = s.get("name", s.get("role", "Stakeholder"))
                    concern = s.get("concern", s.get("key_concern", ""))
                    elements.append(
                        Paragraph(f"<b>{escape(str(name))}</b>: {escape(str(concern))}", styles["BodyText"])
                    )
                else:
                    elements.append(Paragraph(str(s), styles["BodyText"]))

        # Business Goals
        goals = vision_doc.business_goals or []
        if goals:
            elements.append(Paragraph("Business Goals", styles["SectionHead"]))
            table_data = [["#", "Goal", "Priority"]]
            for i, g in enumerate(goals[:15], 1):
                if isinstance(g, dict):
                    table_data.append([
                        str(i),
                        g.get("statement", g.get("goal", str(g)))[:60],
                        g.get("priority", "—"),
                    ])
                else:
                    table_data.append([str(i), str(g)[:60], "—"])
            elements.append(
                self._make_table(table_data, [0.5 * inch, 4.5 * inch, 1.5 * inch])
            )

        # Constraints
        constraints = vision_doc.constraints or {}
        if constraints:
            elements.append(Paragraph("Constraints", styles["SectionHead"]))
            if isinstance(constraints, dict):
                for k, v in list(constraints.items())[:10]:
                    elements.append(
                        Paragraph(f"<b>{escape(str(k))}</b>: {escape(str(v))}", styles["BodyText"])
                    )
            elif isinstance(constraints, list):
                for c in constraints[:10]:
                    elements.append(Paragraph(f"- {c}", styles["BodyText"]))

        # Target Architecture
        if vision_doc.target_architecture_summary:
            elements.append(
                Paragraph("Target Architecture Summary", styles["SectionHead"])
            )
            elements.append(
                Paragraph(vision_doc.target_architecture_summary, styles["BodyText"])
            )

        # Architecture Principles
        principles = vision_doc.architecture_principles or []
        if principles:
            elements.append(
                Paragraph("Architecture Principles", styles["SectionHead"])
            )
            for p in principles[:10]:
                if isinstance(p, dict):
                    elements.append(
                        Paragraph(
                            f"<b>{escape(str(p.get('name', 'Principle')))}</b>: {escape(str(p.get('statement', '')))}",
                            styles["BodyText"],
                        )
                    )
                else:
                    elements.append(Paragraph(f"- {p}", styles["BodyText"]))

        self._build_branded_footer(elements, styles)
        doc.build(elements)
        result = output.getvalue()
        output.close()
        logger.info("Vision Document PDF exported: %s", vision_doc.title)
        return result

    def export_vision_to_excel(self, vision_doc) -> bytes:
        """Export ArchitectureVisionDocument to multi-sheet Excel workbook."""
        if not PANDAS_AVAILABLE:
            raise ImportError("Pandas is required for Excel export")

        output = io.BytesIO()
        with pd.ExcelWriter(output, engine="openpyxl") as writer:
            # Summary sheet
            summary = pd.DataFrame([{
                "Title": vision_doc.title,
                "Scope": vision_doc.scope_summary or "",
                "Target Architecture": vision_doc.target_architecture_summary or "",
                "Status": vision_doc.status,
                "Created": vision_doc.created_at.isoformat() if vision_doc.created_at else "",
            }])
            summary.to_excel(writer, sheet_name="Summary", index=False)

            # Goals sheet
            goals = vision_doc.business_goals or []
            if goals:
                goals_data = []
                for g in goals:
                    if isinstance(g, dict):
                        goals_data.append({
                            "Goal": g.get("statement", g.get("goal", str(g))),
                            "Priority": g.get("priority", ""),
                            "Metric": g.get("metric", ""),
                        })
                    else:
                        goals_data.append({"Goal": str(g), "Priority": "", "Metric": ""})
                pd.DataFrame(goals_data).to_excel(writer, sheet_name="Business Goals", index=False)

            # Stakeholders sheet
            stakeholders = vision_doc.stakeholder_concerns or []
            if stakeholders:
                sh_data = []
                for s in stakeholders:
                    if isinstance(s, dict):
                        sh_data.append({
                            "Stakeholder": s.get("name", s.get("role", "")),
                            "Concern": s.get("concern", s.get("key_concern", "")),
                            "Influence": s.get("influence", ""),
                        })
                    else:
                        sh_data.append({"Stakeholder": str(s), "Concern": "", "Influence": ""})
                pd.DataFrame(sh_data).to_excel(writer, sheet_name="Stakeholders", index=False)

        result = output.getvalue()
        output.close()
        logger.info("Vision Document Excel exported: %s", vision_doc.title)
        return result

    # =========================================================================
    # COMPLIANCE GOVERNANCE REPORT (Phase G)
    # =========================================================================

    def export_compliance_to_pdf(self, report) -> bytes:
        """Export ComplianceGovernanceReport or ComplianceScanReport to PDF.

        Includes severity breakdown chart rendered as a table.
        """
        if not REPORTLAB_AVAILABLE:
            raise ImportError("ReportLab is required for PDF export")

        output = io.BytesIO()
        doc = SimpleDocTemplate(output, pagesize=letter)
        elements = []
        styles = self._get_branded_styles()
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")

        self._build_branded_header(
            elements, styles, report.title,
            "TOGAF ADM Phase G: Implementation Governance", timestamp,
        )

        # Summary metrics
        elements.append(Paragraph("Summary", styles["SectionHead"]))
        total = getattr(report, "total_violations", 0)
        policies = getattr(report, "policies_evaluated", 0)
        scanned = getattr(report, "applications_scanned", 0)
        remediated = getattr(report, "auto_remediated_count", getattr(report, "remediation_tasks_created", 0))

        summary_data = [
            ["Metric", "Value"],
            ["Total Violations", str(total)],
            ["Policies Evaluated", str(policies)],
            ["Applications Scanned", str(scanned)],
            ["Auto-Remediated / Tasks Created", str(remediated)],
        ]
        elements.append(
            self._make_table(summary_data, [3.5 * inch, 2 * inch])
        )

        # Severity breakdown
        sev_data = getattr(report, "violations_by_severity", {}) or {}
        if sev_data:
            elements.append(
                Paragraph("Violations by Severity", styles["SectionHead"])
            )
            sev_table = [["Severity", "Count", "Percentage"]]
            for sev in ["critical", "high", "medium", "low"]:
                count = sev_data.get(sev, 0)
                pct = f"{count / total * 100:.1f}%" if total > 0 else "0%"
                sev_table.append([sev.capitalize(), str(count), pct])
            elements.append(
                self._make_table(sev_table, [2 * inch, 1.5 * inch, 1.5 * inch])
            )

        # Remediation summary
        content = getattr(report, "content", {}) or {}
        remediation = content.get("remediation_summary", content.get("remediation_tasks"))
        if remediation and isinstance(remediation, list):
            elements.append(
                Paragraph("Remediation Tasks", styles["SectionHead"])
            )
            for item in remediation[:15]:
                if isinstance(item, dict):
                    desc = item.get("description", item.get("title", str(item)))
                    elements.append(Paragraph(f"- {desc}", styles["BodyText"]))

        self._build_branded_footer(elements, styles)
        doc.build(elements)
        result = output.getvalue()
        output.close()
        logger.info("Compliance Report PDF exported: %s", report.title)
        return result

    def export_compliance_to_excel(self, report) -> bytes:
        """Export compliance report to multi-sheet Excel workbook."""
        if not PANDAS_AVAILABLE:
            raise ImportError("Pandas is required for Excel export")

        output = io.BytesIO()
        with pd.ExcelWriter(output, engine="openpyxl") as writer:
            summary = pd.DataFrame([{
                "Title": report.title,
                "Total Violations": getattr(report, "total_violations", 0),
                "Policies Evaluated": getattr(report, "policies_evaluated", 0),
                "Scan Scope": getattr(report, "scan_scope", "full"),
                "Status": report.status,
            }])
            summary.to_excel(writer, sheet_name="Summary", index=False)

            sev = getattr(report, "violations_by_severity", {}) or {}
            if sev:
                sev_df = pd.DataFrame([
                    {"Severity": k, "Count": v} for k, v in sev.items()
                ])
                sev_df.to_excel(writer, sheet_name="By Severity", index=False)

        result = output.getvalue()
        output.close()
        logger.info("Compliance Report Excel exported: %s", report.title)
        return result

    # =========================================================================
    # VENDOR SELECTION REPORT
    # =========================================================================

    def export_vendor_selection_to_pdf(self, report) -> bytes:
        """Export VendorSelectionReport to branded PDF with scoring matrix."""
        if not REPORTLAB_AVAILABLE:
            raise ImportError("ReportLab is required for PDF export")

        output = io.BytesIO()
        doc = SimpleDocTemplate(output, pagesize=letter)
        elements = []
        styles = self._get_branded_styles()
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")

        self._build_branded_header(
            elements, styles, report.title,
            "Vendor Selection", timestamp,
        )

        # Capability gap summary
        if report.capability_gap_summary:
            elements.append(
                Paragraph("Capability Gap Summary", styles["SectionHead"])
            )
            elements.append(
                Paragraph(report.capability_gap_summary, styles["BodyText"])
            )

        # Vendor scores table
        scores = report.vendor_scores or {}
        shortlist = scores.get("shortlist", scores.get("scored_vendors", []))
        if shortlist and isinstance(shortlist, list):
            elements.append(
                Paragraph("Vendor Scoring Matrix", styles["SectionHead"])
            )
            score_table = [["Vendor", "Overall Score", "Coverage", "Suitability"]]
            for v in shortlist[:20]:
                if isinstance(v, dict):
                    score_table.append([
                        v.get("vendor_name", v.get("name", "—"))[:30],
                        str(v.get("overall_score", "—")),
                        str(v.get("coverage_score", "—")),
                        str(v.get("suitability_score", "—")),
                    ])
            elements.append(
                self._make_table(
                    score_table,
                    [2.5 * inch, 1.5 * inch, 1.5 * inch, 1.5 * inch],
                )
            )

        # TCO Analysis
        tco = report.tco_analysis or {}
        if tco and isinstance(tco, dict):
            elements.append(Paragraph("TCO Analysis", styles["SectionHead"]))
            for k, v in list(tco.items())[:10]:
                elements.append(
                    Paragraph(f"<b>{escape(str(k))}</b>: {escape(str(v))}", styles["BodyText"])
                )

        # Recommendation
        if report.recommendation:
            elements.append(Paragraph("Recommendation", styles["SectionHead"]))
            elements.append(Paragraph(report.recommendation, styles["BodyText"]))

        if report.decision_rationale:
            elements.append(
                Paragraph("Decision Rationale", styles["SectionHead"])
            )
            elements.append(
                Paragraph(report.decision_rationale, styles["BodyText"])
            )

        self._build_branded_footer(elements, styles)
        doc.build(elements)
        result = output.getvalue()
        output.close()
        logger.info("Vendor Selection PDF exported: %s", report.title)
        return result

    def export_vendor_selection_to_excel(self, report) -> bytes:
        """Export VendorSelectionReport to multi-sheet Excel workbook."""
        if not PANDAS_AVAILABLE:
            raise ImportError("Pandas is required for Excel export")

        output = io.BytesIO()
        with pd.ExcelWriter(output, engine="openpyxl") as writer:
            summary = pd.DataFrame([{
                "Title": report.title,
                "Recommendation": report.recommendation or "",
                "Decision Rationale": report.decision_rationale or "",
                "Status": report.status,
            }])
            summary.to_excel(writer, sheet_name="Summary", index=False)

            scores = report.vendor_scores or {}
            shortlist = scores.get("shortlist", scores.get("scored_vendors", []))
            if shortlist:
                vendor_rows = []
                for v in shortlist:
                    if isinstance(v, dict):
                        vendor_rows.append({
                            "Vendor": v.get("vendor_name", v.get("name", "")),
                            "Overall Score": v.get("overall_score", ""),
                            "Coverage": v.get("coverage_score", ""),
                            "Suitability": v.get("suitability_score", ""),
                        })
                if vendor_rows:
                    pd.DataFrame(vendor_rows).to_excel(
                        writer, sheet_name="Vendor Scores", index=False
                    )

            tco = report.tco_analysis or {}
            if tco and isinstance(tco, dict):
                tco_df = pd.DataFrame([{"Metric": k, "Value": v} for k, v in tco.items()])
                tco_df.to_excel(writer, sheet_name="TCO Analysis", index=False)

        result = output.getvalue()
        output.close()
        logger.info("Vendor Selection Excel exported: %s", report.title)
        return result

    # =========================================================================
    # ARCHITECTURE ROADMAP (Phase E/F)
    # =========================================================================

    def export_roadmap_to_pdf(self, migration_plan) -> bytes:
        """Export MigrationPlanDocument to branded PDF.

        Args:
            migration_plan: MigrationPlanDocument model instance

        Returns:
            PDF content as bytes
        """
        if not REPORTLAB_AVAILABLE:
            raise ImportError("ReportLab is required for PDF export")

        output = io.BytesIO()
        doc = SimpleDocTemplate(output, pagesize=letter)
        elements = []
        styles = self._get_branded_styles()
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")

        phase_label = f"TOGAF ADM Phase {migration_plan.adm_phase}"
        if migration_plan.adm_phase == "E":
            phase_label += ": Opportunities & Solutions"
        elif migration_plan.adm_phase == "F":
            phase_label += ": Migration Planning"

        self._build_branded_header(
            elements, styles, migration_plan.title, phase_label, timestamp,
        )

        # Consolidated gaps
        gaps = migration_plan.consolidated_gaps or []
        if gaps and isinstance(gaps, list):
            elements.append(
                Paragraph("Consolidated Architecture Gaps", styles["SectionHead"])
            )
            gap_table = [["Gap", "Severity", "Layer"]]
            for g in gaps[:20]:
                if isinstance(g, dict):
                    gap_table.append([
                        g.get("description", g.get("gap_name", str(g)))[:50],
                        g.get("severity", g.get("gap_severity", "—")),
                        g.get("layer", "—"),
                    ])
            if len(gap_table) > 1:
                elements.append(
                    self._make_table(
                        gap_table, [3.5 * inch, 1.5 * inch, 1.5 * inch]
                    )
                )

        # Prioritized projects
        projects = migration_plan.prioritized_projects or []
        if projects and isinstance(projects, list):
            elements.append(
                Paragraph("Prioritized Projects", styles["SectionHead"])
            )
            proj_table = [["#", "Project", "Priority"]]
            for i, p in enumerate(projects[:15], 1):
                if isinstance(p, dict):
                    proj_table.append([
                        str(i),
                        p.get("name", p.get("description", str(p)))[:50],
                        str(p.get("remediation_priority", p.get("priority", "—"))),
                    ])
            if len(proj_table) > 1:
                elements.append(
                    self._make_table(
                        proj_table, [0.5 * inch, 4.5 * inch, 1.5 * inch]
                    )
                )

        # Transition architectures
        transitions = migration_plan.transition_architectures or []
        if transitions and isinstance(transitions, list):
            elements.append(
                Paragraph("Transition Architectures", styles["SectionHead"])
            )
            for t in transitions[:5]:
                if isinstance(t, dict):
                    elements.append(
                        Paragraph(
                            f"<b>{escape(str(t.get('name', 'Transition')))}</b>: {escape(str(t.get('description', '')))}",
                            styles["BodyText"],
                        )
                    )

        self._build_branded_footer(elements, styles)
        doc.build(elements)
        result = output.getvalue()
        output.close()
        logger.info("Roadmap PDF exported: %s", migration_plan.title)
        return result

    def export_roadmap_to_excel(self, migration_plan) -> bytes:
        """Export MigrationPlanDocument to multi-sheet Excel workbook."""
        if not PANDAS_AVAILABLE:
            raise ImportError("Pandas is required for Excel export")

        output = io.BytesIO()
        with pd.ExcelWriter(output, engine="openpyxl") as writer:
            summary = pd.DataFrame([{
                "Title": migration_plan.title,
                "ADM Phase": migration_plan.adm_phase,
                "Status": migration_plan.status,
                "Created": migration_plan.created_at.isoformat() if migration_plan.created_at else "",
            }])
            summary.to_excel(writer, sheet_name="Summary", index=False)

            gaps = migration_plan.consolidated_gaps or []
            if gaps:
                gap_rows = []
                for g in gaps:
                    if isinstance(g, dict):
                        gap_rows.append({
                            "Gap": g.get("description", g.get("gap_name", str(g))),
                            "Severity": g.get("severity", g.get("gap_severity", "")),
                            "Layer": g.get("layer", ""),
                        })
                if gap_rows:
                    pd.DataFrame(gap_rows).to_excel(writer, sheet_name="Gaps", index=False)

            projects = migration_plan.prioritized_projects or []
            if projects:
                proj_rows = []
                for p in projects:
                    if isinstance(p, dict):
                        proj_rows.append({
                            "Project": p.get("name", p.get("description", str(p))),
                            "Priority": p.get("remediation_priority", p.get("priority", "")),
                        })
                if proj_rows:
                    pd.DataFrame(proj_rows).to_excel(
                        writer, sheet_name="Projects", index=False
                    )

        result = output.getvalue()
        output.close()
        logger.info("Roadmap Excel exported: %s", migration_plan.title)
        return result
