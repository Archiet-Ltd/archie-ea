"""WaitlistSignup — email-only signup for the public waiting list.

No organisation: a visitor has none. No user link: a visitor is not signed in.
"""

from app import db


class WaitlistSignup(db.Model):
    __tablename__ = "waitlist_signups"

    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(254), unique=True, nullable=False, index=True)
    created_at = db.Column(db.DateTime, default=db.func.now(), nullable=False)
    source = db.Column(db.String(50), default="home_page")
    consent_text = db.Column(db.Text, nullable=False)

    def __repr__(self):
        return f"<WaitlistSignup {self.email}>"
