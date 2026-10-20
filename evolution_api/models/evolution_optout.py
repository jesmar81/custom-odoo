from odoo import _, api, fields, models

from ..utils.phone import phone_tail


class EvolutionOptout(models.Model):
    _name = 'evolution.optout'
    _description = "WhatsApp Opt-out"
    _order = 'id desc'
    _rec_name = 'phone'

    phone = fields.Char(string="Number", required=True, index=True)
    partner_id = fields.Many2one('res.partner', ondelete='set null')
    source = fields.Selection([('incoming', "Asked by WhatsApp"), ('manual', "Added by a person")], default='manual', required=True)
    reason = fields.Char()
    active = fields.Boolean(default=True)

    @api.model
    def _is_opted_out(self, phone):
        return bool(self.search_count([('phone', 'like', phone_tail(phone))]))

    @api.model
    def _register_optout(self, phone, partner, source, reason=None):
        """Remember the refusal and drop what is still waiting to be sent to that number."""
        if self._is_opted_out(phone):
            return self.browse()
        optout = self.create({'phone': phone, 'partner_id': partner.id if partner else False, 'source': source, 'reason': reason})
        pending = self.env['evolution.message'].sudo().search([
            ('phone', 'like', phone_tail(phone)), ('state', 'in', ('draft', 'queued')),
        ])
        pending.write({'state': 'cancelled'})
        if partner and hasattr(partner, 'message_post'):
            partner.sudo().message_post(
                body=_("This contact asked not to receive WhatsApp messages (%s).", reason or ''), subtype_xmlid='mail.mt_note',
            )
        return optout
