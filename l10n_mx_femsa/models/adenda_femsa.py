# -*- coding: utf-8 -*-

from odoo import api, fields, models

class AdendaFemsa(models.Model):
    _inherit = 'account.move'

    l10n_mx_femsa_purchase_order = fields.Char(string='Purchase Order', help='Purchase Order provided by Femsa')
    l10n_mx_femsa_currency = fields.Selection(string='Currency',selection=[
            ('MXP', 'MXN'),
            ('USD', 'USD'),
        ], default='USD', help='Select the currency in which the purchase order or consumer order comes')
    l10n_mx_femsa_entry = fields.Char(string='Entry', help='Entry Number provided by Femsa')
    l10n_mx_femsa_delivery_order = fields.Char(string='Delivery Order', help='Delivery Order provided by Femsa')
    l10n_mx_femsa_consumption_order = fields.Char(string='Consumtion Order', help='Consumption Order provided by Femsa')

    l10n_mx_femsa_show_addenda = fields.Boolean(
        compute='_compute_l10n_mx_femsa_show_addenda',
        help='Technical field: the Femsa addenda is selected on this invoice',
    )

    @api.depends('l10n_mx_edi_addenda_ids')
    def _compute_l10n_mx_femsa_show_addenda(self):
        femsa_addenda = self.env.ref('l10n_mx_femsa.addenda_femsa_autopartes', raise_if_not_found=False)
        for move in self:
            move.l10n_mx_femsa_show_addenda = bool(femsa_addenda) and femsa_addenda in move.l10n_mx_edi_addenda_ids
