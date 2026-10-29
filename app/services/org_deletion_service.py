"""Organization deletion service.

Deletes all tenant-scoped data for an organization before deleting the
organization itself. Uses the canonical tenant table list from
scripts/tenant_tables.txt and deletes in dependency order (leaf tables first).
"""

from pathlib import Path
from sqlalchemy import text, inspect
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session as SASession

from app.extensions import db
from app.models.organization import Organization
from app.models.user import User
from app.models.org_role import OrgRole


class OrganizationDeletionService:
    """Service for deleting an organization and all its tenant data."""

    _tenant_tables_cache = None
    _deletion_order_cache = None

    @classmethod
    def _get_tenant_tables(cls):
        """Load the canonical tenant table list."""
        if cls._tenant_tables_cache is None:
            tenant_tables_path = Path(__file__).parent.parent.parent / "scripts" / "tenant_tables.txt"
            with open(tenant_tables_path) as f:
                cls._tenant_tables_cache = [line.strip() for line in f if line.strip() and not line.startswith("#")]
        return cls._tenant_tables_cache

    @classmethod
    def _get_deletion_tables(cls):
        """Get tenant tables that should be deleted (excludes tables handled specially)."""
        # Users are handled specially (moved to default org), not deleted
        excluded = {"users"}
        return [t for t in cls._get_tenant_tables() if t not in excluded]

    @classmethod
    def _get_deletion_order(cls, engine=None):
        """Determine the correct deletion order based on foreign key dependencies.

        Returns tables ordered so that leaf tables (no other tenant tables
        reference them) are deleted first, then tables they reference, etc.
        """
        if cls._deletion_order_cache is not None:
            return cls._deletion_order_cache

        tenant_tables = set(cls._get_tenant_tables())
        if engine is None:
            engine = db.engine
        inspector = inspect(engine)

        # Build dependency graph: table -> set of tables it references (within tenant_tables)
        deps = {table: set() for table in tenant_tables}
        for table in tenant_tables:
            try:
                fks = inspector.get_foreign_keys(table)
                for fk in fks:
                    ref_table = fk["referred_table"]
                    if ref_table in tenant_tables:
                        deps[table].add(ref_table)
            except OperationalError:
                # Table might not exist in test database yet
                pass

        # Topological sort (Kahn's algorithm)
        # We want to delete leaf tables first, so we reverse the topological order
        in_degree = {table: 0 for table in tenant_tables}
        for table, refs in deps.items():
            for ref in refs:
                in_degree[ref] += 1

        # Start with tables that have no incoming edges (no other tenant tables reference them)
        queue = [table for table, deg in in_degree.items() if deg == 0]
        order = []

        while queue:
            table = queue.pop(0)
            order.append(table)
            for ref in deps[table]:
                in_degree[ref] -= 1
                if in_degree[ref] == 0:
                    queue.append(ref)

        # If there are cycles or missing tables, append remaining in original order
        remaining = [t for t in cls._get_tenant_tables() if t not in order]
        order.extend(remaining)

        cls._deletion_order_cache = order
        return order

    @classmethod
    def delete_organization(cls, org_id, requester_id=None, session=None):
        """Delete an organization and all its tenant data.

        Args:
            org_id: The organization ID to delete.
            requester_id: The user ID performing the deletion (for audit).
            session: Optional SQLAlchemy session to use. If not provided, uses
                Flask-SQLAlchemy's db.session.

        Returns:
            dict: Result with 'success' bool and 'message' str.
        """
        # Use provided session or fall back to Flask-SQLAlchemy session
        sess = session or db.session
        engine = sess.get_bind() if hasattr(sess, 'get_bind') else db.engine

        org = sess.get(Organization, org_id)
        if not org:
            return {"success": False, "message": "Organization not found"}

        if org.slug == "default":
            return {"success": False, "message": "Cannot delete the Default organization"}

        # Get default org for user reassignment
        default_org = sess.query(Organization).filter_by(slug="default").first()
        if not default_org:
            return {"success": False, "message": "Cannot delete — no Default organization to reassign users"}

        try:
            # Step 1: Delete all tenant data in dependency order
            deletion_order = cls._get_deletion_order(engine)
            for table in deletion_order:
                try:
                    sess.execute(
                        text(f"DELETE FROM {table} WHERE organization_id = :org_id"),
                        {"org_id": org_id}
                    )
                except OperationalError:
                    # Table might not exist or have different schema; log and continue
                    pass

            # Step 2: Delete user sessions for users in this organization
            # (user_sessions references users, not just organization)
            user_ids = [u.id for u in sess.query(User).filter_by(organization_id=org_id).all()]
            if user_ids:
                placeholders = ",".join([f":uid{i}" for i in range(len(user_ids))])
                params = {f"uid{i}": uid for i, uid in enumerate(user_ids)}
                sess.execute(
                    text(f"DELETE FROM user_sessions WHERE user_id IN ({placeholders})"),
                    params
                )

            # Step 3: Move users to default org
            users = sess.query(User).filter_by(organization_id=org_id).all()
            moved = 0
            for user in users:
                user.organization_id = default_org.id
                if user.is_admin():
                    user.revoke_org_admin()
                moved += 1

            # Step 4: Delete OrgRole rows for the deleted organization
            sess.query(OrgRole).filter_by(organization_id=org_id).delete(synchronize_session=False)

            # Step 5: Delete the organization
            org_name = org.name
            sess.delete(org)
            sess.commit()

            return {"success": True, "message": f'Organization "{org_name}" deleted. {moved} user(s) moved to Default.'}

        except Exception as e:
            sess.rollback()
            return {"success": False, "message": f"Deletion failed: {str(e)}"}