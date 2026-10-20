from odoo import _, api, fields, models

PREVIEW_LENGTH = 80


class EvolutionConversation(models.Model):
    _name = 'evolution.conversation'
    _description = "WhatsApp Conversation"
    _order = 'last_message_date desc, id desc'

    account_id = fields.Many2one('evolution.account', required=True, index=True, ondelete='cascade')
    phone = fields.Char(string="Number", required=True, index=True)
    partner_id = fields.Many2one('res.partner', ondelete='set null')
    user_id = fields.Many2one('res.users', string="Responsible", ondelete='set null')
    state = fields.Selection([('open', "Open"), ('closed', "Closed")], default='open', required=True)
    message_ids = fields.One2many('evolution.message', 'conversation_id', string="Messages")
    last_message_date = fields.Datetime(readonly=True)
    last_message_preview = fields.Char(readonly=True)
    unread_count = fields.Integer(readonly=True)

    _account_phone_unique = models.Constraint('UNIQUE(account_id, phone)', "One conversation per account and number.")

    @api.depends('partner_id', 'phone')
    def _compute_display_name(self):
        for conversation in self:
            conversation.display_name = f"{conversation.partner_id.display_name or conversation.phone} ({conversation.account_id.name})"

    @api.model
    def _get_or_create(self, account, phone, partner=None):
        conversation = self.search([('account_id', '=', account.id), ('phone', '=', phone)], limit=1)
        if not conversation:
            conversation = self.create({
                'account_id': account.id, 'phone': phone, 'partner_id': partner.id if partner else False,
            })
        elif partner and not conversation.partner_id:
            conversation.partner_id = partner
        return conversation

    @api.model
    def _has_inbound(self, account, phone):
        """True when this number already wrote to the account (so writing back is a reply, not a cold message)."""
        return bool(self.env['evolution.message'].sudo().search_count([
            ('account_id', '=', account.id), ('phone', '=', phone), ('direction', '=', 'in'),
        ]))

    def _register_inbound(self, message):
        self.ensure_one()
        preview = (message.body or ('📎 ' + ', '.join(message.attachment_ids.mapped('name'))))[:PREVIEW_LENGTH]
        self.write({
            'last_message_date': fields.Datetime.now(), 'last_message_preview': preview,
            'unread_count': self.unread_count + 1, 'state': 'open',
        })
        recipients = (self.user_id or self.account_id.user_ids).partner_id
        if recipients:
            for partner in recipients:
                self.env['bus.bus']._sendone(partner, 'simple_notification', {
                    'title': _("WhatsApp: %s", self.partner_id.display_name or self.phone), 'message': preview, 'type': 'info',
                })

    def action_mark_read(self):
        self.write({'unread_count': 0})

    def action_close(self):
        self.write({'state': 'closed'})

    def action_reopen(self):
        self.write({'state': 'open'})

    def action_reply(self):
        self.ensure_one()
        self.unread_count = 0
        return {
            'type': 'ir.actions.act_window', 'name': _("Reply"), 'res_model': 'evolution.composer',
            'view_mode': 'form', 'target': 'new',
            'context': {
                'default_account_id': self.account_id.id, 'default_phone': self.phone,
                'default_partner_id': self.partner_id.id,
            },
        }
