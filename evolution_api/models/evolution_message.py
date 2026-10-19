import logging
import re

import requests

from odoo import _, api, fields, models
from odoo.exceptions import UserError
from odoo.tools import html_escape

from .res_config_settings import PARAM_PREFIX

_logger = logging.getLogger(__name__)

REQUEST_TIMEOUT_SECONDS = 20
MAX_ERROR_LENGTH = 500
PHONE_MATCH_DIGITS = 10
REPLY_LOOKBACK_DAYS = 14
LONG_DISTANCE_PREFIXES = ('00', '011')


def normalize_phone(raw_phone, default_country_code):
    """Digits only, with country code: '(55) 1234-5678' -> '525512345678'."""
    digits = re.sub(r'\D', '', raw_phone or '')
    for prefix in LONG_DISTANCE_PREFIXES:
        if digits.startswith(prefix) and len(digits) > PHONE_MATCH_DIGITS + len(prefix):
            digits = digits[len(prefix):]
    if len(digits) == PHONE_MATCH_DIGITS and default_country_code:
        digits = f'{default_country_code}{digits}'
    return digits


class EvolutionMessage(models.Model):
    _name = 'evolution.message'
    _description = "Evolution API (WhatsApp) Message"
    _order = 'id desc'

    direction = fields.Selection([('out', "Sent"), ('in', "Received")], required=True, readonly=True)
    state = fields.Selection(
        [('sent', "Sent"), ('failed', "Failed"), ('received', "Received")], required=True, readonly=True,
    )
    phone = fields.Char(string="Number", index=True, readonly=True)
    partner_id = fields.Many2one('res.partner', ondelete='set null', readonly=True)
    body = fields.Text(readonly=True)
    external_id = fields.Char(string="WhatsApp Message Id", index='btree_not_null', readonly=True)
    res_model = fields.Char(string="Document Model", readonly=True)
    res_id = fields.Many2oneReference(string="Document", model_field='res_model', readonly=True)
    error = fields.Text(readonly=True)

    # ------------------------------------------------------------
    # CONFIGURATION
    # ------------------------------------------------------------

    @api.model
    def _get_settings(self):
        parameters = self.env['ir.config_parameter'].sudo()
        settings = {
            name: parameters.get_param(f'{PARAM_PREFIX}{name}')
            for name in ('enabled', 'base_url', 'instance', 'api_key', 'default_country_code')
        }
        settings['default_country_code'] = settings['default_country_code'] or '52'
        return settings

    @api.model
    def _get_connection_settings(self):
        settings = self._get_settings()
        if not settings['enabled']:
            raise UserError(_("Evolution API is disabled. Enable it in Settings."))
        missing = [label for name, label in (
            ('base_url', _("Server URL")), ('instance', _("Instance")), ('api_key', _("API Key")),
        ) if not settings[name]]
        if missing:
            raise UserError(_("Evolution API is not configured. Missing: %s", ", ".join(missing)))
        return settings

    # ------------------------------------------------------------
    # SENDING
    # ------------------------------------------------------------

    @api.model
    def _send_text(self, raw_phone, body, partner=None, document=None):
        """Send a text message and log it. Never raises for delivery problems:
        the returned record has ``state == 'failed'`` and the reason in ``error``.
        """
        log_values = {
            'direction': 'out',
            'body': body,
            'partner_id': partner.id if partner else False,
            'res_model': document._name if document else False,
            'res_id': document.id if document else False,
        }
        settings = self._get_connection_settings()
        phone = normalize_phone(raw_phone, settings['default_country_code'])
        log_values['phone'] = phone
        if len(phone) < PHONE_MATCH_DIGITS:
            return self.sudo().create({**log_values, 'state': 'failed', 'error': _("Invalid phone number.")})

        url = f"{settings['base_url'].rstrip('/')}/message/sendText/{settings['instance']}"
        try:
            response = requests.post(
                url,
                headers={'apikey': settings['api_key'], 'Content-Type': 'application/json'},
                json={'number': phone, 'text': body},
                timeout=REQUEST_TIMEOUT_SECONDS,
            )
            response.raise_for_status()
            external_id = (response.json().get('key') or {}).get('id')
            log_values.update(state='sent', external_id=external_id)
        except (requests.exceptions.RequestException, ValueError) as err:
            _logger.warning("Evolution API send failed for %s: %s", phone, err)
            log_values.update(state='failed', error=repr(err)[:MAX_ERROR_LENGTH])
        message = self.sudo().create(log_values)
        if message.state == 'sent' and document is not None and hasattr(document, 'message_post'):
            document.message_post(
                body=_("WhatsApp sent to %(name)s (%(phone)s):<br/>%(body)s",
                       name=partner.display_name if partner else phone, phone=phone, body=message._escaped_body()),
                subtype_xmlid='mail.mt_note',
            )
        return message

    def _escaped_body(self):
        self.ensure_one()
        return html_escape(self.body or '')

    # ------------------------------------------------------------
    # RECEIVING
    # ------------------------------------------------------------

    @api.model
    def _find_partner_by_phone(self, phone):
        phone_tail = phone[-PHONE_MATCH_DIGITS:]
        if len(phone_tail) < PHONE_MATCH_DIGITS:
            return self.env['res.partner']
        candidates = self.env['res.partner'].sudo().search(
            [('phone', 'ilike', phone_tail[-4:])], limit=200,
        )
        for partner in candidates:
            if normalize_phone(partner.phone, '')[-PHONE_MATCH_DIGITS:] == phone_tail:
                return partner
        return self.env['res.partner']

    @api.model
    def _register_incoming(self, raw_phone, body, external_id=None):
        """Store a reply and post it where the conversation started."""
        if external_id and self.sudo().search_count([('external_id', '=', external_id), ('direction', '=', 'in')]):
            return self.browse()  # webhooks are redelivered
        phone = normalize_phone(raw_phone, self._get_settings()['default_country_code'])
        partner = self._find_partner_by_phone(phone)
        last_sent = self.sudo().search([
            ('direction', '=', 'out'), ('state', '=', 'sent'), ('res_model', '!=', False),
            ('phone', 'like', phone[-PHONE_MATCH_DIGITS:]),
            ('create_date', '>=', fields.Datetime.subtract(fields.Datetime.now(), days=REPLY_LOOKBACK_DAYS)),
        ], limit=1)
        message = self.sudo().create({
            'direction': 'in',
            'state': 'received',
            'phone': phone,
            'partner_id': partner.id,
            'body': body,
            'external_id': external_id,
            'res_model': last_sent.res_model,
            'res_id': last_sent.res_id,
        })
        target = self.env[last_sent.res_model].sudo().browse(last_sent.res_id).exists() if last_sent else partner
        if target and hasattr(target, 'message_post'):
            target.message_post(
                body=message._escaped_body(),
                author_id=partner.id or False,
                message_type='comment',
                subtype_xmlid='mail.mt_note',
            )
        return message
