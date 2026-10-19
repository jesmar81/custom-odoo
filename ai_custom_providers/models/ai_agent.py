from odoo import _, api, models
from odoo.exceptions import UserError

from .ai_llm_connection import BUILTIN_PROVIDER_CODES, MODEL_SEPARATOR


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

    def _get_embedding_model(self):
        self.ensure_one()
        if self._get_provider() in BUILTIN_PROVIDER_CODES:
            return super()._get_embedding_model()
        if self.sources_ids:
            raise UserError(_(
                "Knowledge sources are not supported with %(model)s: custom connections have no embedding model. "
                "Remove the sources or choose an OpenAI or Google model.",
                model=self.llm_model,
            ))
        return False
