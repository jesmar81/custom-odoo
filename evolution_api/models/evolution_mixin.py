from odoo import _, models


class EvolutionMixin(models.AbstractModel):
    """Adds the "send by WhatsApp" action to any document model."""
    _name = 'evolution.mixin'
    _description = "WhatsApp sending mixin"

    def _evolution_get_partner(self):
        """The contact that should receive the message of this document."""
        self.ensure_one()
        if self._name == 'res.partner':
            return self
        for field_name in ('partner_id', 'commercial_partner_id'):
            if field_name in self._fields and self[field_name]:
                return self[field_name]
        return self.env['res.partner']

    def action_send_whatsapp(self):
        self.ensure_one()
        partner = self._evolution_get_partner()
        account = self.env['evolution.account']._get_default_account(self)
        return {
            'type': 'ir.actions.act_window',
            'name': _("Send by WhatsApp"),
            'res_model': 'evolution.composer',
            'view_mode': 'form',
            'target': 'new',
            'context': {
                'default_res_model': self._name,
                'default_res_id': self.id,
                'default_partner_id': partner.id,
                'default_phone': partner.phone,
                'default_account_id': account.id,
            },
        }


class ResPartner(models.Model):
    _name = 'res.partner'
    _inherit = ['res.partner', 'evolution.mixin']

    def _evolution_can_contact(self):
        """Return a reason (str) when this contact must NOT receive WhatsApp messages, else False.

        Other modules extend it: it applies to every account, person and AI agent.
        """
        self.ensure_one()
        return False
