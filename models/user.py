from datetime import datetime, timezone
from flask_login import UserMixin
from database import db


class User(UserMixin, db.Model):
    __tablename__ = 'users'

    ROLE_USER = 'user'
    ROLE_ADMIN = 'admin'
    ROLES = (ROLE_USER, ROLE_ADMIN)

    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False, index=True)
    email = db.Column(db.String(120), unique=True, nullable=False, index=True)
    password_hash = db.Column(db.String(256), nullable=False)
    role = db.Column(db.String(20), nullable=False, default=ROLE_USER,
                     server_default=ROLE_USER)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)
    updated_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc),
                           onupdate=lambda: datetime.now(timezone.utc), nullable=False)

    analyses = db.relationship('Analysis', backref='user', lazy='dynamic', cascade='all, delete-orphan')

    @property
    def is_admin(self):
        """Admin status derives solely from the stored role column.

        Roles are never accepted from client input; registration always
        creates ROLE_USER rows.
        """
        return (self.role or self.ROLE_USER) == self.ROLE_ADMIN

    def __repr__(self):
        return f'<User {self.username}>'
