"""SAP Import Orchestrator.

Ties together SapMetadataReader + SapToArchiMateMapper and persists the
resulting ArchiMate elements + relationships to the database.

Usage (Flask route or CLI):

    from app.modules.codegen.services.sap_importer import SapImporter

    # Live SAP system
    result = SapImporter.run_import(
        config={"ashost": "...", "sysnr": "00", "client": "100", "user": "...", "passwd": "...", "lang": "EN"},
        architecture_id=42,
        package_filter="MM",
        mock=False,
    )

    # Mock mode (no SAP required)
    result = SapImporter.run_import(architecture_id=42, mock=True)

The returned dict is safe to JSON-serialise and send directly as an API response.
"""
from __future__ import annotations

import logging
from typing import Optional

from app.extensions import db

logger = logging.getLogger(__name__)


class SapImporter:
    """Orchestrates SAP metadata import into the ArchiMate model store."""

    @staticmethod
    def run_import(
        architecture_id: int,
        config: Optional[dict] = None,
        mock: bool = False,
        package_filter: Optional[str] = None,
        table_limit: int = 500,
        tcode_limit: int = 200,
        role_limit: int = 50,
    ) -> dict:
        """Read SAP metadata, map to ArchiMate, persist to DB.

        Returns a summary dict:
            {
                "ok": True,
                "stats": {"elements": N, "relationships": N, "tables": N, ...},
                "errors": [...],
                "skipped": N,
            }
        """
        # ------------------------------------------------------------------ #
        # 1. Lazy-import services (avoid import-time DB coupling in tests)
        # ------------------------------------------------------------------ #
        try:
            from app.modules.codegen.services.sap_metadata_reader import SapMetadataReader
        except ImportError as exc:
            return {"ok": False, "error": f"sap_metadata_reader not available: {exc}"}

        try:
            from app.modules.codegen.services.sap_to_archimate_mapper import SapToArchiMateMapper
        except ImportError as exc:
            return {"ok": False, "error": f"sap_to_archimate_mapper not available: {exc}"}

        # ------------------------------------------------------------------ #
        # 2. Read SAP metadata
        # ------------------------------------------------------------------ #
        reader_cfg = config or {}
        try:
            with SapMetadataReader(config=reader_cfg, mock=mock) as reader:
                tables = reader.read_tables(package_filter=package_filter, limit=table_limit)
                transactions = reader.read_transactions(
                    module_filter=package_filter, limit=tcode_limit
                )
                roles = reader.read_roles(limit=role_limit)
        except Exception as exc:  # noqa: BLE001
            logger.exception("SAP metadata read failed")
            return {"ok": False, "error": f"SAP read error: {exc}"}

        logger.info(
            "SAP metadata read: %d tables, %d transactions, %d roles",
            len(tables), len(transactions), len(roles),
        )

        # ------------------------------------------------------------------ #
        # 3. Map to ArchiMate
        # ------------------------------------------------------------------ #
        mapper = SapToArchiMateMapper(architecture_id=architecture_id)
        result = mapper.map_all(tables=tables, transactions=transactions, roles=roles)

        # ------------------------------------------------------------------ #
        # 4. Persist elements
        # ------------------------------------------------------------------ #
        from app.models.archimate_core import ArchiMateElement, ArchiMateRelationship  # noqa: PLC0415

        # temp_id → real DB id for relationship resolution
        temp_id_to_db_id: dict[str, int] = {}
        skipped = 0
        db_errors: list[str] = list(result.errors)

        for elem_dict in result.elements:
            temp_id = elem_dict.pop("_temp_id", None)
            name = elem_dict.get("name", "")

            # Dedup by name + type
            existing = (
                db.session.query(ArchiMateElement)
                .filter_by(name=name, type=elem_dict.get("type"))
                .first()
            )
            if existing:
                if temp_id:
                    temp_id_to_db_id[temp_id] = existing.id
                skipped += 1
                continue

            try:
                el = ArchiMateElement(**elem_dict)
                db.session.add(el)
                db.session.flush()
                if temp_id:
                    temp_id_to_db_id[temp_id] = el.id
            except Exception as exc:  # noqa: BLE001
                db.session.rollback()
                db_errors.append(f"Element '{name}': {exc}")

        # ------------------------------------------------------------------ #
        # 5. Persist relationships
        # ------------------------------------------------------------------ #
        rel_count = 0
        for rel_dict in result.relationships:
            source_ref = rel_dict.pop("source_ref", None)
            target_ref = rel_dict.pop("target_ref", None)

            source_id = temp_id_to_db_id.get(source_ref) if source_ref else rel_dict.get("source_id")
            target_id = temp_id_to_db_id.get(target_ref) if target_ref else rel_dict.get("target_id")

            if not source_id or not target_id:
                db_errors.append(
                    f"Skipping relationship {rel_dict.get('type')} — "
                    f"unresolvable refs: {source_ref!r} → {target_ref!r}"
                )
                continue

            # Remove raw id keys that the mapper may have left
            rel_dict.pop("source_id", None)
            rel_dict.pop("target_id", None)

            try:
                rel = ArchiMateRelationship(source_id=source_id, target_id=target_id, **rel_dict)
                db.session.add(rel)
                rel_count += 1
            except Exception as exc:  # noqa: BLE001
                db.session.rollback()
                db_errors.append(f"Relationship {source_id}→{target_id}: {exc}")

        # ------------------------------------------------------------------ #
        # 6. Commit
        # ------------------------------------------------------------------ #
        try:
            db.session.commit()
        except Exception as exc:  # noqa: BLE001
            db.session.rollback()
            logger.exception("DB commit failed during SAP import")
            return {"ok": False, "error": f"DB commit failed: {exc}", "errors": db_errors}

        elements_inserted = len(result.elements)
        summary_stats = {**result.stats, "relationships_created": rel_count, "skipped": skipped}

        logger.info(
            "SAP import complete: %d elements, %d relationships, %d skipped, %d errors",
            elements_inserted, rel_count, skipped, len(db_errors),
        )

        return {
            "ok": True,
            "stats": summary_stats,
            "errors": db_errors,
            "skipped": skipped,
        }


# ---------------------------------------------------------------------------
# Flask route registration — attaches to codegen_bp
# ---------------------------------------------------------------------------

def register_sap_import_routes(app=None) -> None:
    """Register SAP import API routes on codegen_bp.

    Called from the codegen blueprint __init__ or app factory.
    Safe to call multiple times (no-op if already registered).
    """
    try:
        from flask import jsonify, request
        from flask_login import login_required

        from app.modules.codegen.routes.codegen_routes import codegen_bp

        @codegen_bp.route(
            "/api/sap/import",
            methods=["POST"],
            endpoint="sap_import",
        )
        @login_required
        def sap_import_endpoint():
            """Trigger SAP metadata import.

            Body (JSON):
                {
                    "architecture_id": 42,
                    "mock": true,           // optional — use mock data
                    "config": {             // required if mock=false
                        "ashost": "sap.example.com",
                        "sysnr": "00",
                        "client": "100",
                        "user": "RFC_USER",
                        "passwd": "secret",
                        "lang": "EN"
                    },
                    "package_filter": "MM", // optional
                    "table_limit": 500,     // optional
                    "tcode_limit": 200,     // optional
                    "role_limit": 50        // optional
                }
            """
            body = request.get_json(silent=True) or {}
            solution_id = body.get("solution_id") or body.get("architecture_id")
            if not solution_id:
                return jsonify({"ok": False, "error": "solution_id is required"}), 400
            try:
                solution_id = int(solution_id)
            except (ValueError, TypeError):
                return jsonify({"ok": False, "error": "solution_id must be an integer"}), 400

            mock = bool(body.get("mock", False))
            config = body.get("config") if not mock else None

            if not mock and not config:
                return jsonify({"ok": False, "error": "config is required when mock=false"}), 400

            try:
                table_limit = int(body.get("table_limit", 500))
                tcode_limit = int(body.get("tcode_limit", 200))
                role_limit = int(body.get("role_limit", 50))
            except (ValueError, TypeError):
                return jsonify({"ok": False, "error": "table_limit, tcode_limit and role_limit must be integers"}), 400

            # The solution must belong to the signed-in organisation. A missing and a foreign
            # id answer the same 404, and nothing is written before this check passes.
            from app.models.archimate_core import ArchitectureModel  # noqa: PLC0415
            from app.models.solution_models import Solution  # noqa: PLC0415
            from app.utils.tenant import current_organization_id  # noqa: PLC0415

            org_id = current_organization_id()
            solution = (
                Solution.query.filter_by(id=solution_id, organization_id=org_id).first()
                if org_id is not None
                else None
            )
            if solution is None:
                return jsonify({"ok": False, "error": "Solution not found"}), 404

            # Resolve solution → architecture model, creating one if absent.
            arch_model = (
                ArchitectureModel.query
                .filter_by(solution_id=solution_id, organization_id=org_id)
                .order_by(ArchitectureModel.id.asc())
                .first()
            )
            if arch_model is None:
                arch_model = ArchitectureModel(
                    name=f"SAP Architecture — Solution {solution_id}",
                    version="1.0",
                    solution_id=solution_id,
                    organization_id=org_id,
                )
                db.session.add(arch_model)
                db.session.flush()  # get PK before we need it
                logger.info("Created architecture model %s for solution %s", arch_model.id, solution_id)

            architecture_id = arch_model.id

            result = SapImporter.run_import(
                architecture_id=int(architecture_id),
                config=config,
                mock=mock,
                package_filter=body.get("package_filter"),
                table_limit=table_limit,
                tcode_limit=tcode_limit,
                role_limit=role_limit,
            )

            status_code = 200 if result.get("ok") else 500
            return jsonify(result), status_code

    except Exception as exc:  # noqa: BLE001
        logger.warning("Could not register SAP import routes: %s", exc)
