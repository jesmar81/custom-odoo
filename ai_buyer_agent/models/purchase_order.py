from odoo import _, api, fields, models
from odoo.exceptions import UserError


class PurchaseOrder(models.Model):
    _inherit = 'purchase.order'

    ai_buyer_request_id = fields.Many2one('ai.buyer.request', string="Buyer Request", copy=False, index='btree_not_null')
    ai_quote_registered = fields.Boolean(string="Quote Registered", copy=False)
    ai_quote_lead_days = fields.Integer(string="Quoted Lead Time (days)", copy=False)
    ai_quote_notes = fields.Text(string="Quote Conditions", copy=False)
    partner_buys_through_intermediary = fields.Boolean(related='partner_id.buy_through_intermediary')

    def _check_vendors_accept_direct_contact(self):
        blocked_vendors = self.partner_id.filtered('buy_through_intermediary')
        if blocked_vendors:
            raise UserError(_(
                "%s is bought through an intermediary and must not be contacted directly. "
                "Handle this purchase manually with the intermediary.",
                ", ".join(blocked_vendors.mapped('display_name')),
            ))

    def action_rfq_send(self):
        self._check_vendors_accept_direct_contact()
        return super().action_rfq_send()

    def message_post(self, **kwargs):
        # Covers the compose wizard, the buyer agent and any code that marks an RFQ as sent.
        if self.env.context.get('mark_rfq_as_sent'):
            self._check_vendors_accept_direct_contact()
        return super().message_post(**kwargs)

    @api.model
    def _ai_create_rfq(self, partner_id, vendor_reference=None, note=None, expected_date=None):
        vendor = self.env['res.partner'].browse(int(partner_id)).exists()
        if vendor.buy_through_intermediary:
            raise UserError(_(
                "%s is bought through an intermediary: the AI cannot create requests for it. "
                "Tell the user it has to be handled manually.", vendor.display_name,
            ))
        return super()._ai_create_rfq(partner_id, vendor_reference, note, expected_date)
