"""
Alternative Solution: Lazy Loading with MetaData Check

This prevents conflicts by checking if tables already exist before defining them.
"""

from datetime import datetime

from flask import current_app

from app import db


def get_or_create_table(table_name, *columns, **kwargs):
    """Get existing table or create new one if it doesn't exist"""
    if table_name in db.metadata.tables:
        return db.metadata.tables[table_name]

    return db.Table(table_name, *columns, **kwargs)
