from odoo.tests import TransactionCase, tagged

FIREWORKS_MODEL = 'fireworks:accounts/fireworks/models/glm-5p3-flash'


@tagged('post_install', '-at_install')
class TestCustomConnections(TransactionCase):

    def test_model_selection_is_exposed_to_the_client(self):
        """The form reads `fields_get`, which must include the custom models."""
        selection = self.env['ai.agent'].fields_get(['llm_model'])['llm_model']['selection']
        values = [value for value, __ in selection]
        self.assertIn(FIREWORKS_MODEL, values)
        self.assertIn('gpt-4o', values)

    def test_switch_agent_to_custom_connection(self):
        agent = self.env.ref('ai.ai_default_agent')
        agent.llm_model = FIREWORKS_MODEL
        self.assertEqual(agent._get_provider(), 'fireworks')
        self.assertEqual(
            agent._get_embedding_model(),
            self.env.ref('ai_custom_providers.ai_llm_connection_fireworks')._get_embedding_selection_value(),
        )

    def test_connection_in_use_cannot_be_deleted(self):
        agent = self.env['ai.agent'].create({'name': 'Fireworks agent', 'llm_model': FIREWORKS_MODEL})
        with self.assertRaises(Exception):
            self.env.ref('ai_custom_providers.ai_llm_connection_fireworks').unlink()
        self.assertTrue(agent.exists())
