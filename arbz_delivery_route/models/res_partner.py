# -*- coding: utf-8 -*-
from odoo import api, fields, models


class ResPartner(models.Model):
    _inherit = 'res.partner'

    its_vehicle_drivers = fields.Boolean(string='its_vehicle_drivers',)
    its_courier_service = fields.Boolean(string='its_courier_service',)
    code_plus_google_maps = fields.Char(string='code_plus_google_maps',)

    driver_license = fields.Char(string="License ID")
    license_type = fields.Selection([('State', 'state driver`s license '),
            ('federal', ' federal driver`s license')], help='Indicates if the license is issued by the state or by the federation')
    license_subtype = fields.Char(help='Type of license to know which vehicles the driver can operate')
    days_to_expire = fields.Integer(compute='_compute_days_to_expire', help='days left until the driver`s license is sold')
    license_valid_from = fields.Date()
    license_expiration = fields.Date()

    @api.depends('license_expiration')
    def _compute_days_to_expire(self):
        today = fields.Date.context_today(self)
        for record in self:
            if record.license_expiration:
                remaining_days = (record.license_expiration - today).days
                record.days_to_expire = remaining_days if remaining_days > 0 else 0
            else:
                record.days_to_expire = 0
