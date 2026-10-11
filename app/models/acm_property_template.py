"""ACM Property Template — typed property definitions per ArchiMate element type."""
# migration-exempt — new table created via db.create_all() (migration freeze)

from app import db


class AcmPropertyTemplate(db.Model):
    __tablename__ = "acm_property_templates"
    __table_args__ = {"extend_existing": True}

    id = db.Column(db.Integer, primary_key=True)
    archimate_type = db.Column(db.String(64), nullable=False, index=True)
    acm_domain = db.Column(db.String(5), nullable=True, index=True)
    property_key = db.Column(db.String(64), nullable=False)
    display_name = db.Column(db.String(128), nullable=False)
    property_type = db.Column(db.String(16), nullable=False)
    enum_options = db.Column(db.JSON, nullable=True)
    default_value = db.Column(db.String(256), nullable=True)
    required_for_tier = db.Column(db.String(16), nullable=False, default="standard")
    conditional_on_key = db.Column(db.String(64), nullable=True)
    conditional_on_value = db.Column(db.String(256), nullable=True)
    help_text = db.Column(db.Text, nullable=True)
    sort_order = db.Column(db.Integer, default=0)
    # Governed definitions an organisation adds to its own metamodel. NULL is
    # a shared platform template every organisation reads; a value is that
    # organisation's own definition, read by that organisation only (see
    # PropertyService.template_query). Both columns are nullable so
    # reconcile-schema adds them to existing databases and every older row
    # keeps its meaning.
    organization_id = db.Column(
        db.Integer,
        db.ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    # NULL and False both mean "may be left empty".
    is_mandatory = db.Column(db.Boolean, nullable=True)

    def __repr__(self):
        return "<AcmPropertyTemplate %s.%s (%s)>" % (
            self.archimate_type, self.property_key, self.required_for_tier,
        )

    def to_dict(self):
        return {
            "id": self.id,
            "archimate_type": self.archimate_type,
            "acm_domain": self.acm_domain,
            "property_key": self.property_key,
            "display_name": self.display_name,
            "property_type": self.property_type,
            "enum_options": self.enum_options,
            "default_value": self.default_value,
            "required_for_tier": self.required_for_tier,
            "conditional_on_key": self.conditional_on_key,
            "conditional_on_value": self.conditional_on_value,
            "help_text": self.help_text,
            "sort_order": self.sort_order,
            "organization_id": self.organization_id,
            "is_mandatory": bool(self.is_mandatory),
        }
