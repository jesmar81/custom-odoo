from odoo import api, models

from .ai_llm_connection import MODEL_SEPARATOR


class AiAgent(models.Model):
    _inherit = 'ai.agent'

    @api.model
    def _get_llm_model_selection(self):
        selection = super()._get_llm_model_selection()
        connection_models = self.env['ai.llm.connection'].sudo().search([]).model_ids
        for connection_model in connection_models:
            selection.append((
                connection_model._get_selection_value(),
                f"{connection_model.label} ({connection_model.connection_id.name})",
            ))
        return selection

    def _get_provider(self):
        self.ensure_one()
        if MODEL_SEPARATOR in (self.llm_model or ''):
            return self.llm_model.split(MODEL_SEPARATOR, 1)[0]
        return super()._get_provider()
