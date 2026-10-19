# -*- coding: utf-8 -*-
import logging

from odoo.tools.sql import column_exists

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    """Drivers move from res.partner (boolean flag) to hr.employee.

    The old columns are still present here: Odoo drops the columns of removed
    fields only at the end of the upgrade, after the post-migration scripts.
    """
    if not version:
        return

    if column_exists(cr, 'res_partner', 'its_vehicle_drivers'):
        cr.execute("""
            UPDATE hr_employee employee
               SET is_logistics_driver = TRUE,
                   logistics_license_number = partner.driver_license,
                   logistics_license_type = LOWER(partner.license_type),
                   logistics_license_subtype = partner.license_subtype,
                   logistics_license_valid_from = partner.license_valid_from,
                   logistics_license_expiration = partner.license_expiration
              FROM res_partner partner
             WHERE employee.work_contact_id = partner.id
               AND partner.its_vehicle_drivers IS TRUE
        """)
        _logger.info('Logistics: %s employees flagged as drivers from their contact.', cr.rowcount)

        cr.execute("""
            SELECT partner.id, partner.name
              FROM res_partner partner
             WHERE partner.its_vehicle_drivers IS TRUE
               AND NOT EXISTS (SELECT 1 FROM hr_employee employee WHERE employee.work_contact_id = partner.id)
        """)
        for partner_id, partner_name in cr.fetchall():
            _logger.warning('Logistics: driver contact %s (#%s) has no employee; create one and assign '
                            'its stops manually.', partner_name, partner_id)

    if column_exists(cr, 'logistics_route', 'driver_id'):
        cr.execute("""
            UPDATE logistics_route route
               SET driver_employee_id = employee.id,
                   driver_user_id = employee.user_id
              FROM hr_employee employee
             WHERE employee.work_contact_id = route.driver_id
               AND route.driver_employee_id IS NULL
        """)
        _logger.info('Logistics: %s stops linked to their driver employee.', cr.rowcount)
