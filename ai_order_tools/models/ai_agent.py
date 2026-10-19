from odoo import api, models
from odoo.tools import file_open

DEFAULT_PROMPT_PATH = 'ai_order_tools/data/default_agent_prompt.txt'


class AiAgent(models.Model):
    _inherit = 'ai.agent'

    @api.model
    def _ai_order_tools_load_default_prompt(self):
        """Give the default agent its system prompt, unless someone already wrote one."""
        default_agent = self.env.ref('ai.ai_default_agent', raise_if_not_found=False)
        if default_agent and not default_agent.system_prompt:
            with file_open(DEFAULT_PROMPT_PATH) as prompt_file:
                default_agent.system_prompt = prompt_file.read().strip()
