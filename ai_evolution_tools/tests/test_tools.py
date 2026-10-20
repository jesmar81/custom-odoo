import json

from odoo.exceptions import UserError
from odoo.tests import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestAiWhatsappTools(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.user = cls.env['res.users'].create({
            'name': 'Agent User', 'login': 'agent_user_wa', 'email': 'agent@example.com',
            'group_ids': [(6, 0, [cls.env.ref('evolution_api.group_evolution_user').id])],
        })
        cls.outsider = cls.env['res.users'].create({
            'name': 'No Access', 'login': 'no_access_wa',
            'group_ids': [(6, 0, [cls.env.ref('evolution_api.group_evolution_user').id])],
        })
        cls.account = cls.env['evolution.account'].create({
            'name': 'Cobranza', 'base_url': 'https://evo.example.com', 'instance': 'cobranza', 'api_key': 'k',
            'user_ids': [(6, 0, cls.user.ids)], 'send_from_hour': 0.0, 'send_until_hour': 24.0, 'tz': 'UTC',
        })
        cls.customer = cls.env['res.partner'].create({'name': 'Cliente Moroso', 'phone': '55 3333 4444'})

    def _tool(self, xml_id, arguments, user=None):
        # the agent reads the tool as sudo, but the action runs with the rights of the real user
        tool = self.env.ref(f'ai_evolution_tools.{xml_id}').with_user(user or self.user).sudo()
        return json.loads(tool._ai_tool_run(None, arguments))

    def test_the_agent_gets_the_tools_but_none_sends(self):
        tool_names = set(self.env.ref('ai.ai_default_agent').sudo().topic_ids.tool_ids._get_ai_tools())
        self.assertLessEqual({'ai_whatsapp_propose_message', 'ai_whatsapp_read_conversation'}, tool_names)
        self.assertFalse([name for name in tool_names if name.startswith('ai_whatsapp') and 'send' in name])

    def test_propose_creates_a_draft_and_sends_nothing(self):
        answer = self._tool('ai_whatsapp_propose_message', {'partner_id': float(self.customer.id), 'text': "Hola, le recordamos su factura."})
        self.assertIn("PENDING APPROVAL", answer['status'])
        draft = self.env['evolution.message'].browse(answer['draft_id'])
        self.assertEqual((draft.state, draft.proposed_by, draft.author_id, draft.account_id), ('draft', 'AI agent', self.user, self.account))
        self.assertEqual(self.env['evolution.message'].search_count([('state', '=', 'queued')]), 0)
        pending = self._tool('ai_whatsapp_list_drafts', {})
        self.assertEqual([item['draft_id'] for item in pending], [draft.id])

    def test_policy_is_enforced_for_the_agent(self):
        self.env['evolution.optout']._register_optout('525533334444', self.customer, source='manual')
        with self.assertRaisesRegex(UserError, "asked not to receive"):
            self._tool('ai_whatsapp_propose_message', {'partner_id': self.customer.id, 'text': "Hola"})
        no_phone = self.env['res.partner'].create({'name': 'Sin telefono'})
        with self.assertRaisesRegex(UserError, "no phone"):
            self._tool('ai_whatsapp_propose_message', {'partner_id': no_phone.id, 'text': "Hola"})

    def test_user_without_an_account_cannot_propose(self):
        with self.assertRaisesRegex(UserError, "do not have access"):
            self._tool('ai_whatsapp_propose_message', {'partner_id': self.customer.id, 'text': "Hola"}, user=self.outsider)

    def test_conversations_are_found_and_read_within_the_users_access(self):
        self.env['evolution.message']._register_incoming(self.account, '525533334444', "Mañana les deposito")
        found = self._tool('ai_whatsapp_find_conversation', {'search_text': 'Moroso'})
        self.assertEqual(len(found['conversations']), 1)
        conversation_id = found['conversations'][0]['id']
        read = self._tool('ai_whatsapp_read_conversation', {'conversation_id': conversation_id})
        self.assertEqual(read['messages'][-1]['text'], "Mañana les deposito")
        self.assertEqual(read['messages'][-1]['direction'], 'received')
        self.assertEqual(self._tool('ai_whatsapp_find_conversation', {'search_text': 'Moroso'}, user=self.outsider)['conversations'], [])
        accounts = self._tool('ai_whatsapp_list_accounts', {})
        self.assertEqual([item['name'] for item in accounts], ['Cobranza'])
