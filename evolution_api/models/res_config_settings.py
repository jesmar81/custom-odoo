from odoo import fields, models

DEFAULT_OPTOUT_WORDS = "baja, stop, no mas mensajes, no más mensajes, cancelar suscripcion, cancelar suscripción"


class ResConfigSettings(models.TransientModel):
    _inherit = 'res.config.settings'

    evolution_optout_words = fields.Char(
        string="Opt-out words",
        config_parameter='evolution_api.optout_words',
        default=DEFAULT_OPTOUT_WORDS,
        groups='base.group_system',
        help="A reply that is exactly one of these words stops all further messages to that number.",
    )
    evolution_max_file_mb = fields.Integer(
        string="Maximum file size (MB)",
        config_parameter='evolution_api.max_file_mb',
        default=16,
        groups='base.group_system',
    )
