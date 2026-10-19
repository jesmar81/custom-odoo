from odoo import api, models
from odoo.tools import file_open

BUYER_PROMPT_PATH = 'ai_buyer_agent/data/buyer_agent_prompt.txt'


class AiAgent(models.Model):
    _inherit = 'ai.agent'

    @api.model
    def _ai_buyer_load_default_prompt(self):
        """Fill the buyer agent prompt only when it is empty, so manual edits survive updates."""
        buyer_agent = self.env.ref('ai_buyer_agent.ai_agent_buyer', raise_if_not_found=False)
        if buyer_agent and not buyer_agent.system_prompt:
            with file_open(BUYER_PROMPT_PATH) as prompt_file:
                buyer_agent.system_prompt = prompt_file.read().strip()
