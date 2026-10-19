from odoo import api, fields, models

FIREWORKS_CONNECTION_CODE = 'fireworks'


class ResConfigSettings(models.TransientModel):
    _inherit = 'res.config.settings'

    fireworks_key_enabled = fields.Boolean(
        string="Enable Fireworks AI API key",
        groups='base.group_system',
    )
    fireworks_key = fields.Char(
        string="Fireworks AI API key",
        groups='base.group_system',
    )

    @api.model
    def get_values(self):
        settings_values = super().get_values()
        connection = self.env['ai.llm.connection']._get_by_code(FIREWORKS_CONNECTION_CODE)
        settings_values.update(
            fireworks_key=connection.api_key or False,
            fireworks_key_enabled=bool(connection.api_key),
        )
        return settings_values

    def set_values(self):
        super().set_values()
        connection = self.env['ai.llm.connection']._get_by_code(FIREWORKS_CONNECTION_CODE)
        if connection:
            connection.api_key = self.fireworks_key if self.fireworks_key_enabled else False
