import json

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError


class ResPartner(models.Model):
    _inherit = 'res.partner'

    buy_through_intermediary = fields.Boolean(
        string="Buy through an intermediary",
        tracking=True,
        help="This vendor does not supply us directly. Nothing is ever sent to it by the AI buyer, "
             "and requests for quotation cannot be sent to it from Odoo: they are handled manually "
             "with the intermediary.",
    )
    intermediary_partner_id = fields.Many2one(
        'res.partner', string="Intermediary", tracking=True,
        help="Person or company that buys from this vendor on our behalf (e.g. Luis Alberto).",
    )

    @api.constrains('intermediary_partner_id')
    def _check_intermediary_is_not_self(self):
        for partner in self:
            if partner.intermediary_partner_id == partner:
                raise ValidationError(_("A vendor cannot be its own intermediary."))

    def _evolution_can_contact(self):
        """Plugs the intermediary flag into every WhatsApp account, person and AI agent."""
        self.ensure_one()
        if self.buy_through_intermediary:
            return _(
                "%s is bought through an intermediary and must not be contacted directly.", self.display_name,
            )
        return super()._evolution_can_contact()

    @api.model
    def _ai_find_partners(self, search_text, role=None):
        """Tell the agent which partners must never be contacted directly."""
        answer = super()._ai_find_partners(search_text, role)
        try:
            payload = json.loads(answer)
        except ValueError:
            return answer
        flagged_partners = self.browse(
            [partner['id'] for partner in payload.get('partners', [])]
        ).filtered('buy_through_intermediary')
        for partner in payload.get('partners', []):
            if partner['id'] in flagged_partners.ids:
                partner['contact_directly'] = False
                partner['intermediary'] = self.browse(partner['id']).intermediary_partner_id.display_name or None
                partner['warning'] = "Bought through an intermediary: the AI must not create or send anything to it."
        return json.dumps(payload, ensure_ascii=False)
