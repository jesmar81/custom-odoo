from odoo import api, fields, models

from odoo.addons.ai.models import ai_embedding as base_ai_embedding
from odoo.addons.ai.utils.llm_providers import EMBEDDING_MODELS_SELECTION

from .ai_llm_connection import MODEL_SEPARATOR

_original_get_provider_for_embedding_model = base_ai_embedding.get_provider_for_embedding_model
_original_get_embedding_config = base_ai_embedding.get_embedding_config


def _get_provider_for_embedding_model(env, embedding_model):
    if MODEL_SEPARATOR in (embedding_model or ''):
        return embedding_model.split(MODEL_SEPARATOR, 1)[0]
    return _original_get_provider_for_embedding_model(env, embedding_model)


def _get_embedding_config(env, provider):
    connection = env['ai.llm.connection']._get_by_code(provider)
    if connection:
        return {
            'max_batch_size': connection.embedding_max_batch_size or 32,
            'max_tokens_per_request': connection.embedding_max_tokens_per_request or 30000,
        }
    return _original_get_embedding_config(env, provider)


# `ai.embedding` imported these helpers by name, so they are rebound in its namespace.
base_ai_embedding.get_provider_for_embedding_model = _get_provider_for_embedding_model
base_ai_embedding.get_embedding_config = _get_embedding_config


class AiEmbedding(models.Model):
    _inherit = 'ai.embedding'

    embedding_model = fields.Selection(selection='_get_embedding_model_selection')

    @api.model
    def _get_embedding_model_selection(self):
        selection = list(EMBEDDING_MODELS_SELECTION)
        for connection in self.env['ai.llm.connection'].sudo().with_context(active_test=False).search([]):
            if embedding_value := connection._get_embedding_selection_value():
                selection.append((embedding_value, f"{connection.name} ({connection.embedding_model_identifier})"))
        return selection

    @api.autovacuum
    def _cron_remove_deprecated_models_embeddings_and_recompute(self):
        """Same cleanup as the base one, but custom connection models are valid."""
        valid_models = [value for value, __ in self._get_embedding_model_selection()]
        embeddings_to_unlink = self.search([('embedding_model', 'not in', valid_models)])
        checksum_providers_to_embed = set()
        for embedding in embeddings_to_unlink:
            try:
                provider = _get_provider_for_embedding_model(self.env, embedding.embedding_model)
            except Exception:  # model of a provider that no longer exists: nothing to re-embed
                continue
            checksum_providers_to_embed.add((embedding.checksum, provider))
        embeddings_to_unlink.unlink()

        candidate_sources = self.env['ai.agent.source'].search([
            ('attachment_id.checksum', 'in', [checksum for checksum, __ in checksum_providers_to_embed]),
        ])
        sources_to_embed = candidate_sources.filtered(
            lambda source: (source.attachment_id.checksum, source.agent_id._get_provider()) in checksum_providers_to_embed
        )
        sources_to_embed.write({
            'status': 'processing',
            'is_active': False,
            'error_details': False,
        })
        self.env.ref('ai.ir_cron_generate_embedding')._trigger()
