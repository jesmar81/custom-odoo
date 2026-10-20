from odoo import _, api, models
from odoo.exceptions import UserError
from odoo.tools import html2plaintext

from odoo.addons.ai_order_tools.utils.order_helpers import browse_existing, dump_json

MAX_CONVERSATIONS = 10
MAX_MESSAGES = 20
AI_PROPOSER_LABEL = "AI agent"


class EvolutionMessage(models.Model):
    _inherit = 'evolution.message'

    @api.model
    def _ai_list_accounts(self):
        accounts = self.env['evolution.account']._get_usable_accounts()
        return dump_json([{
            'id': account.id, 'name': account.name, 'connection': account.connection_state,
            'paused': account.is_paused, 'remaining_today': account.remaining_today,
            'accepts_ai_drafts': account.allow_ai_drafts,
        } for account in accounts])

    @api.model
    def _ai_find_conversations(self, search_text):
        search_text = (search_text or '').strip()
        if len(search_text) < 2:
            raise UserError(_("Provide at least 2 characters (name or phone)."))
        conversations = self.env['evolution.conversation'].search([
            '|', ('partner_id.name', 'ilike', search_text), ('phone', 'ilike', search_text),
        ], limit=MAX_CONVERSATIONS)
        return dump_json({
            'conversations': [{
                'id': conversation.id,
                'contact': conversation.partner_id.display_name or conversation.phone,
                'partner_id': conversation.partner_id.id or None,
                'account': conversation.account_id.name,
                'last_message_date': conversation.last_message_date,
                'unread': conversation.unread_count,
            } for conversation in conversations],
            'note': None if conversations else "No conversation found. Use 'Find Partner' and propose a first message instead.",
        })

    @api.model
    def _ai_read_conversation(self, conversation_id):
        conversation = browse_existing(self.env['evolution.conversation'], conversation_id, _("Conversation"))
        messages = conversation.message_ids.filtered(lambda message: message.state != 'cancelled').sorted('id')[-MAX_MESSAGES:]
        return dump_json({
            'contact': conversation.partner_id.display_name or conversation.phone,
            'account': conversation.account_id.name,
            'messages': [{
                'date': message.create_date,
                'direction': 'received' if message.direction == 'in' else 'sent',
                'state': message.state,
                'text': html2plaintext(message.body or ''),
                'files': message.attachment_ids.mapped('name'),
            } for message in messages],
        })

    @api.model
    def _ai_propose_message(self, partner_id, text, account_id=None):
        partner = browse_existing(self.env['res.partner'], partner_id, _("Contact"))
        if not partner.phone:
            raise UserError(_("%s has no phone number.", partner.display_name))
        accounts = self.env['evolution.account']
        if account_id is not None:
            accounts = browse_existing(self.env['evolution.account'], account_id, _("WhatsApp account"))
        else:
            accounts = self.env['evolution.account']._get_default_account()
        if not accounts:
            raise UserError(_("You do not have access to any WhatsApp account."))
        draft = self._queue_message(
            accounts, partner.phone, text, partner=partner, author=self.env.user, draft=True, proposed_by=AI_PROPOSER_LABEL,
        )
        return dump_json({
            'draft_id': draft.id,
            'status': "PENDING APPROVAL: nothing has been sent.",
            'account': accounts.name,
            'to': partner.display_name,
            'text': text,
            'next_step': "A person must open WhatsApp > Pending approval in Odoo and approve it.",
            'url': f"{draft.get_base_url()}/odoo/evolution.message/{draft.id}",
        })

    @api.model
    def _ai_list_drafts(self):
        drafts = self.search([('state', '=', 'draft')], limit=MAX_CONVERSATIONS)
        return dump_json([{
            'draft_id': draft.id, 'to': draft.partner_id.display_name or draft.phone,
            'account': draft.account_id.name, 'text': draft.body,
        } for draft in drafts])
