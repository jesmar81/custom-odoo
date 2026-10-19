# -*- coding: utf-8 -*-
from odoo.tools.sql import column_exists, rename_column


def migrate(cr, version):
    if not version:
        return
    # attendant -> attendant_id (required field, keep the data before the ORM adds the new column)
    if column_exists(cr, 'logistics_route', 'attendant') and not column_exists(cr, 'logistics_route', 'attendant_id'):
        rename_column(cr, 'logistics_route', 'attendant', 'attendant_id')
