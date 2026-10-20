import base64
import logging
import random
import re
from datetime import timedelta

import requests

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError
from odoo.tools import config, html_escape

from ..utils.phone import PHONE_MATCH_DIGITS, normalize_phone, phone_tail
from .evolution_account import FAILURES_BEFORE_PAUSE, MAX_ERROR_LENGTH
from .res_config_settings import DEFAULT_OPTOUT_WORDS

_logger = logging.getLogger(__name__)

MAX_SEND_ATTEMPTS = 3
RETRY_BACKOFF_MINUTES = 5
REPLY_LOOKBACK_DAYS = 14
TYPING_DELAY_MILLISECONDS = (1200, 3500)
DEFAULT_MAX_FILE_MB = 16
MEDIA_TYPES = (('image/', 'image'), ('video/', 'video'), ('audio/', 'audio'))

__all__ = ['EvolutionMessage', 'normalize_phone']  # normalize_phone kept importable from here


class EvolutionMessage(models.Model):
    _name = 'evolution.message'
    _description = "WhatsApp Message (Evolution API)"
    _order = 'id desc'

    account_id = fields.Many2one('evolution.account', required=True, index=True, ondelete='restrict', readonly=True)
    conversation_id = fields.Many2one('evolution.conversation', index=True, ondelete='set null', readonly=True)
    direction = fields.Selection([('out', "Sent"), ('in', "Received")], required=True, readonly=True)
    state = fields.Selection(
        [
            ('draft', "Pending approval"),
            ('queued', "Queued"),
            ('sent', "Sent"),
            ('failed', "Failed"),
            ('cancelled', "Cancelled"),
            ('received', "Received"),
        ],
        required=True, readonly=True, index=True,
    )
    phone = fields.Char(string="Number", index=True, readonly=True)
    partner_id = fields.Many2one('res.partner', ondelete='set null', readonly=True)
    body = fields.Text(readonly=True)
    attachment_ids = fields.Many2many('ir.attachment', string="Files", readonly=True)
    external_id = fields.Char(string="WhatsApp Message Id", index='btree_not_null', readonly=True)
    res_model = fields.Char(string="Document Model", readonly=True)
    res_id = fields.Many2oneReference(string="Document", model_field='res_model', readonly=True)
    error = fields.Text(readonly=True)
    attempts = fields.Integer(readonly=True)

    author_id = fields.Many2one('res.users', string="Requested by", readonly=True, ondelete='set null')
    proposed_by = fields.Char(string="Proposed by (AI agent)", readonly=True)
    approved_by_id = fields.Many2one('res.users', readonly=True, ondelete='set null')
    queued_date = fields.Datetime(readonly=True, index=True)
    sent_date = fields.Datetime(readonly=True)
    retry_after = fields.Datetime(readonly=True)

    # ------------------------------------------------------------
    # QUEUING
    # ------------------------------------------------------------

    @api.model
    def _find_partner_by_phone(self, phone):
        phone_end = phone_tail(phone)
        if len(phone_end) < PHONE_MATCH_DIGITS:
            return self.env['res.partner']
        candidates = self.env['res.partner'].sudo().search([('phone', 'ilike', phone_end[-4:])], limit=200)
        for partner in candidates:
            if phone_tail(normalize_phone(partner.phone, '')) == phone_end:
                return partner
        return self.env['res.partner']

    @api.model
    def _split_in_parts(self, body, attachments):
        """One text message, or one message per file (the text becomes the caption of a single file)."""
        attachments = attachments or self.env['ir.attachment']
        if not attachments:
            return [(body, self.env['ir.attachment'])]
        if len(attachments) == 1:
            return [(body, attachments)]
        parts = [(body, self.env['ir.attachment'])] if body else []
        return parts + [(False, attachment) for attachment in attachments]

    @api.model
    def _queue_message(self, account, raw_phone, body, partner=None, document=None, attachments=None,
                       author=None, draft=False, proposed_by=None, skip_contact_check=False):
        """Validate and store outgoing messages.

        :param draft: wait for an explicit approval instead of being sent
        :raises UserError: when the policy of the account forbids the message
        :return: the created ``evolution.message`` records
        """
        account = account.sudo()
        author = author or self.env.user
        account._check_user_can_use(author)
        phone = account._normalize(raw_phone)
        parts = self._split_in_parts(body, attachments)
        account._check_can_queue(phone, partner, body, parts=0 if draft else len(parts), skip_contact_check=skip_contact_check)
        if draft and not account.allow_ai_drafts and proposed_by:
            raise UserError(_("The account %s does not accept drafts proposed by AI agents.", account.name))
        partner = partner or self._find_partner_by_phone(phone)
        conversation = self.env['evolution.conversation'].sudo()._get_or_create(account, phone, partner)
        queued_date = False if draft else fields.Datetime.now()
        messages = self.sudo().create([{
            'account_id': account.id,
            'conversation_id': conversation.id,
            'direction': 'out',
            'state': 'draft' if draft else 'queued',
            'phone': phone,
            'partner_id': partner.id if partner else False,
            'body': part_body,
            'attachment_ids': [(6, 0, part_attachments.ids)],
            'res_model': document._name if document else False,
            'res_id': document.id if document else False,
            'author_id': author.id,
            'proposed_by': proposed_by,
            'queued_date': queued_date,
        } for part_body, part_attachments in parts])
        # Files are owned by the message: the wizard that uploaded them is deleted by the autovacuum.
        for message, (__, part_attachments) in zip(messages, parts):
            part_attachments.sudo().write({'res_model': 'evolution.message', 'res_id': message.id})
        if not draft:
            self._trigger_queue()
        return messages

    @api.model
    def _trigger_queue(self, at=None):
        self.env.ref('evolution_api.ir_cron_evolution_send_queue').sudo()._trigger(at=at)

    def action_approve(self):
        """A person accepts the drafts proposed by an AI agent (or by a colleague)."""
        for message in self:
            if message.state != 'draft':
                raise UserError(_("Only drafts can be approved."))
            message.account_id._check_user_can_use(self.env.user)
            message.account_id._check_can_queue(message.phone, message.partner_id, message.body)
            message.sudo().write({
                'state': 'queued', 'queued_date': fields.Datetime.now(), 'approved_by_id': self.env.user.id,
            })
        self._trigger_queue()
        return True

    def action_reject(self):
        for message in self:
            message.account_id._check_user_can_use(self.env.user)
            if message.state not in ('draft', 'queued', 'failed'):
                raise UserError(_("This message can no longer be cancelled."))
            message.sudo().state = 'cancelled'
        return True

    def action_retry(self):
        for message in self:
            if message.state != 'failed':
                raise UserError(_("Only failed messages can be retried."))
            message.account_id._check_user_can_use(self.env.user)
            message.account_id._check_can_queue(message.phone, message.partner_id, False)
            message.sudo().write({'state': 'queued', 'attempts': 0, 'error': False, 'queued_date': fields.Datetime.now()})
        self._trigger_queue()
        return True

    # ------------------------------------------------------------
    # SENDING (cron)
    # ------------------------------------------------------------

    @api.model
    def _cron_send_queue(self):
        """Send at most one message per account per run, respecting window, spacing and daily cap."""
        now = fields.Datetime.now()
        queued_accounts = self.sudo().search([('state', '=', 'queued')]).account_id
        for account in queued_accounts:
            if account.is_paused or not account.active or not account._is_within_send_window():
                continue
            if account.next_send_at and account.next_send_at > now:
                self._trigger_queue(at=account.next_send_at)
                continue
            if account._count_messages_today(['sent'], 'sent_date') >= account.daily_limit:
                continue
            message = self.sudo().search([
                ('account_id', '=', account.id), ('state', '=', 'queued'),
                '|', ('retry_after', '=', False), ('retry_after', '<=', now),
            ], order='queued_date, id', limit=1)
            if not message:
                continue
            message._send_now()
            account.sudo().next_send_at = fields.Datetime.now() + account._random_spacing()
            self._commit_after_external_call()
            if self.sudo().search_count([('account_id', '=', account.id), ('state', '=', 'queued')]):
                self._trigger_queue(at=account.next_send_at)

    @api.model
    def _commit_after_external_call(self):
        """The HTTP call cannot be rolled back, so the matching state must be saved right away.

        Tests run in a single transaction that must not be committed.
        """
        if not config['test_enable']:
            self.env.cr.commit()

    def _build_request(self):
        """Return (path, body) for Evolution API."""
        self.ensure_one()
        delay = random.randint(*TYPING_DELAY_MILLISECONDS)
        if not self.attachment_ids:
            return '/message/sendText', {'number': self.phone, 'text': self.body or '', 'delay': delay}
        attachment = self.attachment_ids[0]
        max_bytes = int(self.env['ir.config_parameter'].sudo().get_param('evolution_api.max_file_mb') or DEFAULT_MAX_FILE_MB) * 1024 * 1024
        if attachment.file_size > max_bytes:
            raise UserError(_("%s is larger than the allowed file size.", attachment.name))
        mimetype = attachment.mimetype or 'application/octet-stream'
        media_type = next((kind for prefix, kind in MEDIA_TYPES if mimetype.startswith(prefix)), 'document')
        return '/message/sendMedia', {
            'number': self.phone,
            'mediatype': media_type,
            'mimetype': mimetype,
            'fileName': attachment.name,
            'caption': self.body or '',
            'media': base64.b64encode(attachment.raw).decode(),
            'delay': delay,
        }

    def _send_now(self):
        self.ensure_one()
        account = self.account_id
        try:
            path, body = self._build_request()
            answer = account._api_request('POST', f'/{path.strip("/")}', body)
            self.write({
                'state': 'sent', 'sent_date': fields.Datetime.now(), 'attempts': self.attempts + 1,
                'external_id': (answer.get('key') or {}).get('id'), 'error': False,
            })
            account.consecutive_failures = 0
            self._post_in_document(_("WhatsApp sent"))
        except (requests.exceptions.RequestException, ValueError, UserError) as err:
            _logger.warning("Evolution API: sending to %s failed: %s", self.phone, err)
            self._register_failure(err)

    def _register_failure(self, err):
        self.ensure_one()
        account = self.account_id
        attempts = self.attempts + 1
        error = (err.args[0] if isinstance(err, UserError) else repr(err))[:MAX_ERROR_LENGTH]
        gave_up = attempts >= MAX_SEND_ATTEMPTS or isinstance(err, UserError)
        self.write({
            'attempts': attempts, 'error': error, 'state': 'failed' if gave_up else 'queued',
            'retry_after': False if gave_up else fields.Datetime.now() + timedelta(minutes=RETRY_BACKOFF_MINUTES * attempts),
        })
        account.consecutive_failures += 1
        if gave_up:
            self._post_in_document(_("WhatsApp NOT sent"), error=error)
        if account.consecutive_failures >= FAILURES_BEFORE_PAUSE:
            account.write({'is_paused': True, 'pause_reason': _("Paused after %s consecutive failures.", account.consecutive_failures)})
            account.message_post(
                body=_("Sending paused after %s consecutive failures. Check the number in Evolution API, then resume.",
                       account.consecutive_failures),
                subtype_xmlid='mail.mt_comment',
            )

    def _post_in_document(self, title, error=None):
        self.ensure_one()
        if not (self.res_model and self.res_id and self.res_model in self.env):
            return
        document = self.env[self.res_model].sudo().browse(self.res_id).exists()
        if document and hasattr(document, 'message_post'):
            detail = html_escape(error) if error else html_escape(self.body or '')
            files = ", ".join(html_escape(name) for name in self.attachment_ids.mapped('name'))
            document.message_post(
                body=f"{html_escape(title)}: {html_escape(self.partner_id.display_name or self.phone)}<br/>{detail}"
                     + (f"<br/>📎 {files}" if files else ""),
                subtype_xmlid='mail.mt_note',
            )

    # ------------------------------------------------------------
    # RECEIVING
    # ------------------------------------------------------------

    @api.model
    def _register_incoming(self, account, raw_phone, body, external_id=None, files=None):
        """Store a reply, link it to its conversation and post it in the document it answers.

        :param files: list of (file name, mimetype, raw bytes) received with the message
        """
        account = account.sudo()
        if external_id and self.sudo().search_count([
            ('account_id', '=', account.id), ('external_id', '=', external_id), ('direction', '=', 'in'),
        ]):
            return self.browse()  # webhooks are redelivered
        phone = account._normalize(raw_phone)
        partner = self._find_partner_by_phone(phone)
        conversation = self.env['evolution.conversation'].sudo()._get_or_create(account, phone, partner)
        last_sent = self.sudo().search([
            ('conversation_id', '=', conversation.id), ('direction', '=', 'out'), ('state', '=', 'sent'),
            ('res_model', '!=', False),
            ('create_date', '>=', fields.Datetime.subtract(fields.Datetime.now(), days=REPLY_LOOKBACK_DAYS)),
        ], limit=1)
        attachments = self.env['ir.attachment'].sudo().create([{
            'name': name, 'mimetype': mimetype, 'raw': raw, 'res_model': 'evolution.message', 'res_id': 0,
        } for name, mimetype, raw in files or []])
        message = self.sudo().create({
            'account_id': account.id,
            'conversation_id': conversation.id,
            'direction': 'in',
            'state': 'received',
            'phone': phone,
            'partner_id': partner.id,
            'body': body,
            'attachment_ids': [(6, 0, attachments.ids)],
            'external_id': external_id,
            'res_model': last_sent.res_model,
            'res_id': last_sent.res_id,
        })
        attachments.write({'res_id': message.id})
        conversation._register_inbound(message)
        message._detect_opt_out()
        target = self.env[last_sent.res_model].sudo().browse(last_sent.res_id).exists() if last_sent else partner
        if target and hasattr(target, 'message_post'):
            target.message_post(
                body=html_escape(body or ''), attachment_ids=attachments.ids,
                author_id=partner.id or False, message_type='comment', subtype_xmlid='mail.mt_note',
            )
        return message

    def _detect_opt_out(self):
        self.ensure_one()
        words = self.env['ir.config_parameter'].sudo().get_param('evolution_api.optout_words') or DEFAULT_OPTOUT_WORDS
        stop_words = {re.sub(r'\s+', ' ', word.strip().lower()) for word in words.split(',') if word.strip()}
        if re.sub(r'\s+', ' ', (self.body or '').strip().lower()) in stop_words:
            self.env['evolution.optout'].sudo()._register_optout(self.phone, self.partner_id, source='incoming', reason=self.body)
