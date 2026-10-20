import logging
import random
import secrets
from datetime import timedelta

import pytz
import requests

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tools import format_datetime

from ..utils.phone import normalize_phone, phone_tail

_logger = logging.getLogger(__name__)

REQUEST_TIMEOUT_SECONDS = 20
MAX_ERROR_LENGTH = 500
CONNECTION_STATES = [
    ('unknown', "Unknown"),
    ('open', "Connected"),
    ('connecting', "Connecting"),
    ('close', "Disconnected"),
]
FAILURES_BEFORE_PAUSE = 3


class EvolutionAccount(models.Model):
    _name = 'evolution.account'
    _description = "WhatsApp Account (Evolution API instance)"
    _inherit = ['mail.thread']
    _order = 'name'

    name = fields.Char(string="Name", required=True, tracking=True, help="Area or purpose, e.g. Purchases, Collections.")
    active = fields.Boolean(default=True)
    company_id = fields.Many2one('res.company', default=lambda self: self.env.company, required=True)
    phone_number = fields.Char(string="WhatsApp Number", help="Informative: the number linked to the instance.")

    # Connection
    base_url = fields.Char(string="Server URL", required=True, help="e.g. https://evolution.mycompany.com")
    instance = fields.Char(string="Instance", required=True)
    api_key = fields.Char(string="API Key", groups='base.group_system')
    webhook_token = fields.Char(copy=False, groups='base.group_system', default=lambda self: secrets.token_urlsafe(32))
    webhook_url = fields.Char(compute='_compute_webhook_url', groups='base.group_system')
    connection_state = fields.Selection(CONNECTION_STATES, default='unknown', readonly=True, tracking=True)
    is_paused = fields.Boolean(
        string="Paused", readonly=True, tracking=True,
        help="Sending stops by itself after repeated failures. A supervisor resumes it after checking the number.",
    )
    pause_reason = fields.Char(readonly=True)
    consecutive_failures = fields.Integer(readonly=True)

    # Who can use it
    user_ids = fields.Many2many('res.users', string="Allowed users", help="Supervisors can always use every account.")
    default_model_ids = fields.Many2many(
        'ir.model', string="Default for documents",
        help="Pre-selected when someone sends a WhatsApp from these documents.",
    )

    # Protection against being banned
    daily_limit = fields.Integer(string="Daily limit", default=50, help="Maximum messages sent per day by this number.")
    max_per_contact_per_day = fields.Integer(string="Per contact per day", default=5)
    min_delay_seconds = fields.Integer(string="Minimum spacing (s)", default=30)
    max_delay_seconds = fields.Integer(string="Maximum spacing (s)", default=90)
    send_from_hour = fields.Float(string="Send from", default=8.0)
    send_until_hour = fields.Float(string="Send until", default=20.0)
    tz = fields.Selection(
        lambda self: [(tz, tz) for tz in sorted(pytz.all_timezones, key=lambda tz: tz if not tz.startswith('Etc/') else '_')],
        string="Time zone", default=lambda self: self.env.user.tz or 'America/Mexico_City', required=True,
    )
    require_known_contact = fields.Boolean(
        string="Known contacts only", default=True,
        help="Only send to numbers of existing contacts or numbers that already wrote to this account.",
    )
    allow_ai_drafts = fields.Boolean(
        string="Accept AI drafts", default=True,
        help="AI agents may propose messages from this account. A person always approves before they are sent.",
    )
    default_country_code = fields.Char(string="Default country code", default='52')
    next_send_at = fields.Datetime(readonly=True, copy=False)

    sent_today = fields.Integer(compute='_compute_usage_today')
    remaining_today = fields.Integer(compute='_compute_usage_today')
    queued_count = fields.Integer(compute='_compute_queued_count')
    test_phone = fields.Char(string="Test number")

    _daily_limit_positive = models.Constraint('CHECK(daily_limit >= 0)', "The daily limit cannot be negative.")
    _instance_unique = models.Constraint('UNIQUE(base_url, instance)', "This instance is already configured.")

    @api.constrains('min_delay_seconds', 'max_delay_seconds', 'send_from_hour', 'send_until_hour')
    def _check_limits(self):
        for account in self:
            if account.min_delay_seconds < 0 or account.max_delay_seconds < account.min_delay_seconds:
                raise ValidationError(_("The maximum spacing must be at least the minimum spacing."))
            if not 0 <= account.send_from_hour < account.send_until_hour <= 24:
                raise ValidationError(_("The sending hours must satisfy 0 <= from < until <= 24."))

    @api.constrains('base_url')
    def _check_base_url(self):
        for account in self:
            if not (account.base_url or '').startswith(('https://', 'http://')):
                raise ValidationError(_("The server URL must start with https:// or http://"))

    # ------------------------------------------------------------
    # COMPUTES
    # ------------------------------------------------------------

    def _compute_webhook_url(self):
        base_url = self.env['ir.config_parameter'].sudo().get_param('web.base.url', '')
        for account in self:
            account.webhook_url = (
                f'{base_url}/evolution_api/webhook?token={account.webhook_token}' if account.webhook_token else False
            )

    def _get_local_now(self):
        self.ensure_one()
        return pytz.utc.localize(fields.Datetime.now()).astimezone(pytz.timezone(self.tz or 'UTC'))

    def _get_day_start(self):
        """Start of the account's local day, as a naive UTC datetime."""
        self.ensure_one()
        local_start = self._get_local_now().replace(hour=0, minute=0, second=0, microsecond=0)
        return local_start.astimezone(pytz.utc).replace(tzinfo=None)

    def _count_messages_today(self, states, date_field):
        self.ensure_one()
        return self.env['evolution.message'].sudo().search_count([
            ('account_id', '=', self.id), ('direction', '=', 'out'),
            ('state', 'in', states), (date_field, '>=', self._get_day_start()),
        ])

    @api.depends('daily_limit')
    def _compute_usage_today(self):
        for account in self:
            sent = account._count_messages_today(['queued', 'sent'], 'queued_date') if account.id else 0
            account.sent_today = sent
            account.remaining_today = max(account.daily_limit - sent, 0)

    def _compute_queued_count(self):
        counts = dict(self.env['evolution.message'].sudo()._read_group(
            [('account_id', 'in', self.ids), ('state', '=', 'queued')], ['account_id'], ['__count'],
        ))
        for account in self:
            account.queued_count = counts.get(account, 0)

    # ------------------------------------------------------------
    # ACCESS
    # ------------------------------------------------------------

    def _user_can_use(self, user):
        self.ensure_one()
        return (
            user in self.sudo().user_ids
            or user.has_group('evolution_api.group_evolution_manager')
        )

    def _check_user_can_use(self, user):
        for account in self:
            if not account._user_can_use(user):
                raise AccessError(_("You are not allowed to use the WhatsApp account %s.", account.name))

    @api.model
    def _get_usable_accounts(self, user=None):
        user = user or self.env.user
        return self.sudo().search([]).filtered(lambda account: account._user_can_use(user))

    @api.model
    def _get_default_account(self, document=None, user=None):
        """The account to preselect: the one declared for the document model, then any usable one."""
        usable = self._get_usable_accounts(user)
        if document is not None and document:
            for account in usable:
                if document._name in account.default_model_ids.mapped('model'):
                    return account
        return usable[:1]

    # ------------------------------------------------------------
    # POLICY: everything that keeps the number safe
    # ------------------------------------------------------------

    def _is_within_send_window(self):
        self.ensure_one()
        local_now = self._get_local_now()
        hour = local_now.hour + local_now.minute / 60
        return self.send_from_hour <= hour < self.send_until_hour

    def _next_window_start(self):
        self.ensure_one()
        local_now = self._get_local_now()
        hour = local_now.hour + local_now.minute / 60
        start_hour, start_minute = divmod(round(self.send_from_hour * 60), 60)
        opening = local_now.replace(hour=start_hour, minute=start_minute, second=0, microsecond=0)
        if hour >= self.send_until_hour:
            opening += timedelta(days=1)
        return opening.astimezone(pytz.utc).replace(tzinfo=None)

    def _check_can_queue(self, phone, partner, body, parts=1, skip_contact_check=False):
        """Raise a UserError explaining why this message must not be queued."""
        self.ensure_one()
        Message = self.env['evolution.message'].sudo()
        if not self.active:
            raise UserError(_("The WhatsApp account %s is archived.", self.name))
        if self.is_paused:
            raise UserError(_("The account %(name)s is paused: %(reason)s", name=self.name, reason=self.pause_reason or ''))
        if len(phone) < 10:
            raise UserError(_("Invalid phone number."))
        if self.env['evolution.optout'].sudo()._is_opted_out(phone):
            raise UserError(_("This number asked not to receive messages."))
        partner = partner or Message._find_partner_by_phone(phone)
        if partner and (reason := partner._evolution_can_contact()):
            raise UserError(reason)
        if self.require_known_contact and not skip_contact_check and not (
            partner or self.env['evolution.conversation'].sudo()._has_inbound(self, phone)
        ):
            raise UserError(_(
                "This number is not a known contact and has never written to this account. "
                "To protect the number, messages to unknown numbers are blocked.",
            ))
        used_today = self._count_messages_today(['queued', 'sent'], 'queued_date')
        if used_today + parts > self.daily_limit:
            raise UserError(_(
                "The daily limit of %(limit)s messages of %(name)s would be exceeded (%(used)s already used today).",
                limit=self.daily_limit, name=self.name, used=used_today,
            ))
        contact_messages = Message.search([
            ('account_id', '=', self.id), ('direction', '=', 'out'), ('state', 'in', ('queued', 'sent')),
            ('queued_date', '>=', self._get_day_start()), ('phone', 'like', phone_tail(phone)),
        ])
        if len(contact_messages) + parts > self.max_per_contact_per_day:
            raise UserError(_(
                "Too many messages to this contact today (limit %s). Wait until tomorrow.", self.max_per_contact_per_day,
            ))
        if body and contact_messages.filtered(lambda message: message.body == body):
            raise UserError(_("The same message was already sent or queued to this contact today."))

    # ------------------------------------------------------------
    # EVOLUTION API CALLS
    # ------------------------------------------------------------

    def _api_request(self, method, path, body=None):
        """Call Evolution API. Raises requests exceptions; never logs the API key."""
        self.ensure_one()
        api_key = self.sudo().api_key
        if not api_key:
            raise UserError(_("The account %s has no API key.", self.name))
        response = requests.request(
            method,
            f"{self.base_url.rstrip('/')}/{path.strip('/')}/{self.instance}",
            headers={'apikey': api_key, 'Content-Type': 'application/json'},
            json=body,
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        return response.json()

    @staticmethod
    def _find_state(payload):
        """The state key sits at different depths depending on the Evolution version."""
        if isinstance(payload, dict):
            if isinstance(payload.get('state'), str):
                return payload['state']
            for nested in payload.values():
                if (state := EvolutionAccount._find_state(nested)):
                    return state
        return False

    def _update_connection_state(self, state):
        self.ensure_one()
        if state in dict(CONNECTION_STATES) and state != self.connection_state:
            self.sudo().connection_state = state
            if state == 'close':
                self.sudo().message_post(
                    body=_("The WhatsApp number was disconnected. Scan the QR code again in Evolution API."),
                    subtype_xmlid='mail.mt_comment',
                )

    def action_check_connection(self):
        self.ensure_one()
        try:
            state = self._find_state(self._api_request('GET', '/instance/connectionState'))
        except (requests.exceptions.RequestException, ValueError) as err:
            raise UserError(_("Could not reach Evolution API: %s", repr(err)[:MAX_ERROR_LENGTH]))
        self._update_connection_state(state or 'unknown')
        return {
            'type': 'ir.actions.client', 'tag': 'display_notification',
            'params': {'title': self.name, 'message': _("Connection state: %s", dict(CONNECTION_STATES).get(state, state)),
                       'type': 'success' if state == 'open' else 'warning'},
        }

    @api.model
    def _cron_check_connections(self):
        for account in self.search([('is_paused', '=', False)]):
            try:
                account._update_connection_state(self._find_state(account._api_request('GET', '/instance/connectionState')))
            except (requests.exceptions.RequestException, ValueError, UserError) as err:
                _logger.warning("Evolution API: connection check failed for %s: %s", account.name, err)

    # ------------------------------------------------------------
    # ACTIONS
    # ------------------------------------------------------------

    def action_resume(self):
        self.write({'is_paused': False, 'pause_reason': False, 'consecutive_failures': 0})
        self.env.ref('evolution_api.ir_cron_evolution_send_queue')._trigger()

    def action_regenerate_token(self):
        self.ensure_one()
        self.webhook_token = secrets.token_urlsafe(32)

    def action_send_test(self):
        self.ensure_one()
        if not self.test_phone:
            raise UserError(_("Enter a test number first."))
        self.env['evolution.message']._queue_message(
            self, self.test_phone, _("Test message sent from Odoo."), author=self.env.user, skip_contact_check=True,
        )
        return {
            'type': 'ir.actions.client', 'tag': 'display_notification',
            'params': {'title': self.name, 'message': _("Test message queued."), 'type': 'success'},
        }

    def action_view_messages(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window', 'name': _("Messages"), 'res_model': 'evolution.message',
            'view_mode': 'list,form', 'domain': [('account_id', '=', self.id)],
            'context': {'default_account_id': self.id},
        }

    def _random_spacing(self):
        self.ensure_one()
        return timedelta(seconds=random.uniform(self.min_delay_seconds, self.max_delay_seconds))

    def _normalize(self, raw_phone):
        self.ensure_one()
        return normalize_phone(raw_phone, self.default_country_code)

    def _format_local(self, moment):
        return format_datetime(self.env, moment, tz=self.tz)
