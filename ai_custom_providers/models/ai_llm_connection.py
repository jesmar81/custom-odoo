import re

from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError

BUILTIN_PROVIDER_CODES = ('openai', 'google')
MODEL_SEPARATOR = ':'


class AiLlmConnection(models.Model):
    _name = 'ai.llm.connection'
    _description = "AI LLM Connection (OpenAI-compatible)"
    _order = 'name'

    name = fields.Char(required=True, translate=False)
    code = fields.Char(
        string="Technical Code",
        required=True,
        help="Short unique identifier (lowercase letters, digits, '_' or '-'). "
             "It prefixes the model value stored on agents, e.g. 'fireworks:accounts/...'.",
    )
    base_url = fields.Char(
        string="Base URL",
        required=True,
        help="Root of the OpenAI-compatible API, without '/chat/completions'. "
             "Fireworks AI: https://api.fireworks.ai/inference/v1",
    )
    api_key = fields.Char(string="API Key", groups='base.group_system')
    active = fields.Boolean(default=True)
    model_ids = fields.One2many('ai.llm.connection.model', 'connection_id', string="Models")

    _code_unique = models.Constraint('UNIQUE(code)', "The technical code must be unique.")

    @api.constrains('code')
    def _check_code(self):
        for connection in self:
            if not re.fullmatch(r'[a-z0-9_-]+', connection.code or ''):
                raise ValidationError(_("The code may only contain lowercase letters, digits, '_' and '-'."))
            if connection.code in BUILTIN_PROVIDER_CODES:
                raise ValidationError(_("'%s' is reserved for a built-in provider.", connection.code))

    @api.constrains('base_url')
    def _check_base_url(self):
        for connection in self:
            if not (connection.base_url or '').startswith(('https://', 'http://')):
                raise ValidationError(_("The base URL must start with https:// or http://"))

    @api.ondelete(at_uninstall=False)
    def _unlink_except_used_by_agent(self):
        for connection in self:
            used_agents = self.env['ai.agent'].with_context(active_test=False).search_count(
                [('llm_model', '=like', f'{connection.code}{MODEL_SEPARATOR}%')]
            )
            if used_agents:
                raise UserError(_("%(connection)s is used by %(count)s AI agent(s).", connection=connection.name, count=used_agents))

    def _get_api_key(self):
        self.ensure_one()
        api_key = self.sudo().api_key
        if not api_key:
            raise UserError(_("No API key set for connection '%s'.", self.name))
        return api_key

    @api.model
    def _get_by_code(self, code):
        return self.sudo().search([('code', '=', code)], limit=1)


class AiLlmConnectionModel(models.Model):
    _name = 'ai.llm.connection.model'
    _description = "AI LLM Connection Model"
    _order = 'sequence, id'

    connection_id = fields.Many2one('ai.llm.connection', required=True, ondelete='cascade')
    sequence = fields.Integer(default=10)
    model_identifier = fields.Char(
        string="Model ID",
        required=True,
        help="Exact model identifier of the provider, e.g. accounts/fireworks/models/llama-v3p3-70b-instruct",
    )
    label = fields.Char(required=True)

    _identifier_unique = models.Constraint(
        'UNIQUE(connection_id, model_identifier)', "This model is already listed for the connection.",
    )

    def _get_selection_value(self):
        self.ensure_one()
        return f'{self.connection_id.code}{MODEL_SEPARATOR}{self.model_identifier}'
