import secrets

from odoo import _, api, fields, models
from odoo.exceptions import UserError

PARAM_PREFIX = 'evolution_api.'
WEBHOOK_TOKEN_PARAM = f'{PARAM_PREFIX}webhook_token'


class ResConfigSettings(models.TransientModel):
    _inherit = 'res.config.settings'

    evolution_enabled = fields.Boolean(
        string="Send WhatsApp with Evolution API",
        config_parameter=f'{PARAM_PREFIX}enabled',
        groups='base.group_system',
    )
    evolution_base_url = fields.Char(
        string="Server URL",
        config_parameter=f'{PARAM_PREFIX}base_url',
        groups='base.group_system',
        help="Address of your Evolution API server, e.g. https://evolution.mycompany.com",
    )
    evolution_instance = fields.Char(
        string="Instance",
        config_parameter=f'{PARAM_PREFIX}instance',
        groups='base.group_system',
        help="Name of the WhatsApp instance created in Evolution API.",
    )
    evolution_api_key = fields.Char(
        string="API Key",
        config_parameter=f'{PARAM_PREFIX}api_key',
        groups='base.group_system',
        help="Instance key sent in the 'apikey' header.",
    )
    evolution_default_country_code = fields.Char(
        string="Default Country Code",
        config_parameter=f'{PARAM_PREFIX}default_country_code',
        default='52',
        groups='base.group_system',
        help="Added to 10-digit numbers that have no country code (52 = Mexico).",
    )
    evolution_webhook_url = fields.Char(
        string="Webhook URL",
        compute='_compute_evolution_webhook_url',
        groups='base.group_system',
        help="Configure this URL in your Evolution API instance for the MESSAGES_UPSERT event.",
    )
    evolution_test_number = fields.Char(string="Test Number", groups='base.group_system')

    @api.model
    def _evolution_ensure_webhook_token(self):
        parameters = self.env['ir.config_parameter'].sudo()
        if not parameters.get_param(WEBHOOK_TOKEN_PARAM):
            parameters.set_param(WEBHOOK_TOKEN_PARAM, secrets.token_urlsafe(32))

    @api.depends_context('uid')
    def _compute_evolution_webhook_url(self):
        parameters = self.env['ir.config_parameter'].sudo()
        token = parameters.get_param(WEBHOOK_TOKEN_PARAM)
        base_url = parameters.get_param('web.base.url', '')
        for settings in self:
            settings.evolution_webhook_url = f'{base_url}/evolution_api/webhook?token={token}' if token else False

    def action_evolution_regenerate_token(self):
        self.env['ir.config_parameter'].sudo().set_param(WEBHOOK_TOKEN_PARAM, secrets.token_urlsafe(32))
        return {'type': 'ir.actions.client', 'tag': 'reload'}

    def action_evolution_send_test(self):
        self.ensure_one()
        if not self.evolution_test_number:
            raise UserError(_("Enter a test number first."))
        self.execute()  # save the connection settings before using them
        message = self.env['evolution.message']._send_text(
            self.evolution_test_number, _("Test message sent from Odoo."),
        )
        if message.state == 'failed':
            raise UserError(_("The test message failed: %s", message.error))
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {'title': _("Evolution API"), 'message': _("Test message sent."), 'type': 'success'},
        }
