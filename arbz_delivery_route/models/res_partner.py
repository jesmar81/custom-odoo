# -*- coding: utf-8 -*-
from odoo import fields, models


class ResPartner(models.Model):
    _inherit = 'res.partner'

    its_courier_service = fields.Boolean(string='Is Courier Service')
