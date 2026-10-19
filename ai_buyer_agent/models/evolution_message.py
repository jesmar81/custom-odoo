from odoo import _, api, models
from odoo.exceptions import UserError


class EvolutionMessage(models.Model):
    _inherit = 'evolution.message'

    @api.model
    def _send_text(self, raw_phone, body, partner=None, document=None):
        if partner and partner.buy_through_intermediary:
            raise UserError(_(
                "%s is bought through an intermediary and must not be contacted directly.", partner.display_name,
            ))
        return super()._send_text(raw_phone, body, partner=partner, document=document)
