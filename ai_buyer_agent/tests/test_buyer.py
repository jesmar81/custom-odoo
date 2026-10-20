import json
from datetime import timedelta
from unittest.mock import MagicMock, patch

from odoo import fields
from odoo.exceptions import AccessError, UserError
from odoo.tests import TransactionCase, tagged

HTTP_PATH = 'odoo.addons.evolution_api.models.evolution_account.requests.request'


def _evolution_ok():
    response = MagicMock()
    response.json.return_value = {'key': {'id': 'WA-1'}}
    return response


@tagged('post_install', '-at_install')
class TestBuyerAgent(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env = cls.env(user=cls.env.ref('base.user_admin'))
        cls.account = cls.env['evolution.account'].create({
            'name': 'Compras', 'base_url': 'https://evo.example.com', 'instance': 'compras', 'api_key': 'secret',
            'default_model_ids': [(6, 0, cls.env['ir.model']._get('purchase.order').ids)],
            'daily_limit': 50, 'min_delay_seconds': 0, 'max_delay_seconds': 0,
            'send_from_hour': 0.0, 'send_until_hour': 24.0, 'tz': 'UTC',
        })
        Partner = cls.env['res.partner']
        cls.vendor_whatsapp = Partner.create({'name': 'Vendor A', 'phone': '55 1111 0001', 'email': 'a@example.com'})
        cls.vendor_email = Partner.create({'name': 'Vendor B', 'email': 'b@example.com'})
        cls.vendor_third = Partner.create({'name': 'Vendor E', 'phone': '55 1111 0005'})
        cls.vendor_fourth = Partner.create({'name': 'Vendor F', 'phone': '55 1111 0006'})
        cls.vendor_luis = Partner.create({
            'name': 'Vendor C (via Luis)', 'phone': '55 1111 0003', 'buy_through_intermediary': True,
            'intermediary_partner_id': Partner.create({'name': 'Luis Alberto'}).id,
        })
        cls.vendor_no_contact = Partner.create({'name': 'Vendor D'})
        cls.product = cls.env['product.product'].create({'name': 'Tornillo Especial', 'purchase_ok': True})
        cls.other_product = cls.env['product.product'].create({'name': 'Tuerca Especial', 'purchase_ok': True})
        # purchase history: A x3, B x2, F x1 (older), E x1 (more recent, wins the tie), flagged C x5, unreachable D x4
        for vendor, times in ((cls.vendor_fourth, 1), (cls.vendor_whatsapp, 3), (cls.vendor_email, 2),
                              (cls.vendor_third, 1), (cls.vendor_luis, 5), (cls.vendor_no_contact, 4)):
            for __ in range(times):
                cls._confirmed_purchase(vendor, cls.product, price=50.0)

    @classmethod
    def _confirmed_purchase(cls, vendor, product, price):
        order = cls.env['purchase.order'].create({
            'partner_id': vendor.id,
            'order_line': [(0, 0, {'product_id': product.id, 'product_qty': 1, 'price_unit': price})],
        })
        order.button_confirm()
        return order

    def _flush_whatsapp(self, cycles=3):
        with patch(HTTP_PATH, return_value=_evolution_ok()) as request_mock:
            for __ in range(cycles):
                self.env['evolution.message']._cron_send_queue()
        return request_mock

    def _start(self, product=None, needed_in_days=10):
        needed_date = fields.Date.to_string(fields.Date.today() + timedelta(days=needed_in_days))
        answer = self.env['ai.buyer.request']._ai_start_request((product or self.product).id, 100, needed_date, "Urgente")
        return self.env['ai.buyer.request'].browse(json.loads(answer)['id'])

    # ------------------------------------------------------------ selection

    def test_vendor_selection_from_history(self):
        selected, flagged, unreachable = self.env['ai.buyer.request']._select_vendors(self.product)
        self.assertEqual(selected, self.vendor_whatsapp | self.vendor_email | self.vendor_third)  # 3 most frequent reachable
        self.assertNotIn(self.vendor_fourth, selected)
        self.assertEqual(flagged, self.vendor_luis)
        self.assertEqual(unreachable, self.vendor_no_contact)

    def test_start_request_prepares_three_drafts_and_sends_nothing(self):
        messages_before = self.env['evolution.message'].search_count([])
        with patch(HTTP_PATH) as post:
            request = self._start()
            self._flush_whatsapp()
        post.assert_not_called()
        self.assertEqual(self.env['evolution.message'].search_count([]), messages_before)
        self.assertEqual(request.state, 'to_approve')
        self.assertEqual(len(request.order_ids), 3)
        self.assertEqual(set(request.order_ids.mapped('state')), {'draft'})
        self.assertNotIn(self.vendor_luis, request.order_ids.partner_id)
        self.assertEqual(request.skipped_partner_ids, self.vendor_luis)
        self.assertEqual(request.order_ids[0].alternative_po_ids, request.order_ids)

    def test_only_flagged_vendors_leaves_the_purchase_manual(self):
        product = self.other_product
        self._confirmed_purchase(self.vendor_luis, product, 10.0)
        request = self._start(product)
        self.assertEqual(request.state, 'manual')
        self.assertFalse(request.order_ids)
        with self.assertRaises(UserError):
            request.action_approve_and_send()

    def test_one_open_request_per_product(self):
        self._start()
        with self.assertRaisesRegex(UserError, "already an open request"):
            self._start()

    # ------------------------------------------------------------ intermediary flag everywhere

    def test_flagged_vendor_is_blocked_on_every_path(self):
        order = self.env['purchase.order'].create({'partner_id': self.vendor_luis.id})
        with self.assertRaisesRegex(UserError, "intermediary"):
            order.action_rfq_send()
        with self.assertRaisesRegex(UserError, "intermediary"):
            order.with_context(mark_rfq_as_sent=True).message_post(body="x")
        with self.assertRaisesRegex(UserError, "intermediary"):
            self.env['evolution.message']._queue_message(self.account, self.vendor_luis.phone, "hola", partner=self.vendor_luis)
        with self.assertRaisesRegex(UserError, "intermediary"):  # only the number is known: found by it
            self.env['evolution.message']._queue_message(self.account, '5511110003', "hola")
        with self.assertRaisesRegex(UserError, "intermediary"):
            self.env['purchase.order']._ai_create_rfq(self.vendor_luis.id)
        self.assertEqual(order.state, 'draft')

    def test_find_partner_marks_flagged_vendors(self):
        answer = json.loads(self.env['res.partner']._ai_find_partners('Vendor C'))
        partner = answer['partners'][0]
        self.assertFalse(partner['contact_directly'])
        self.assertEqual(partner['intermediary'], 'Luis Alberto')

    def test_vendor_flagged_after_the_draft_blocks_the_approval(self):
        request = self._start()
        request.order_ids[0].partner_id.buy_through_intermediary = True
        with self.assertRaisesRegex(UserError, "intermediary"):
            request.action_approve_and_send()
        self.assertFalse(self.env['evolution.message'].search([('res_model', '=', 'purchase.order')]))
        self.assertEqual(request.state, 'to_approve')

    # ------------------------------------------------------------ approval and sending

    def test_approve_queues_whatsapp_or_email_and_starts_the_wait(self):
        request = self._start()
        request.action_approve_and_send()
        self.assertEqual(request.state, 'waiting')
        self.assertEqual(request.deadline_date - request.sent_date, timedelta(hours=24))
        self.assertEqual(set(request.order_ids.mapped('state')), {'sent'})
        queued = self.env['evolution.message'].search([('res_model', '=', 'purchase.order'), ('state', '=', 'queued')])
        self.assertEqual(sorted(queued.mapped('phone')), ['525511110001', '525511110005'])  # A and E; B has no phone
        self.assertEqual(set(queued.mapped('account_id')), {self.account})
        mail_order = request.order_ids.filtered(lambda o: o.partner_id == self.vendor_email)
        self.assertTrue(mail_order.message_ids.filtered(lambda m: m.message_type == 'comment'))  # email path
        request_mock = self._flush_whatsapp()
        self.assertEqual(request_mock.call_count, 2)
        self.assertIn('Tornillo Especial', request_mock.call_args_list[0].kwargs['json']['text'])
        self.assertEqual(set(self.env['evolution.message'].search([('id', 'in', queued.ids)]).mapped('state')), {'sent'})

    def test_whatsapp_refused_by_the_account_falls_back_to_email(self):
        self.env['evolution.optout']._register_optout('525511110001', self.vendor_whatsapp, source='manual')
        request = self._start()
        request.action_approve_and_send()
        order_a = request.order_ids.filtered(lambda o: o.partner_id == self.vendor_whatsapp)
        self.assertEqual(order_a.state, 'sent')  # reached by email
        self.assertTrue(order_a.message_ids.filtered(lambda m: m.message_type == 'comment'))
        order_e = request.order_ids.filtered(lambda o: o.partner_id == self.vendor_third)
        self.assertEqual(order_e.state, 'sent')  # E has no email but its WhatsApp was queued

    def test_approver_without_access_to_a_whatsapp_account_uses_email(self):
        self.account.default_model_ids = False
        self.account.user_ids = False
        buyer = self.env['res.users'].create({
            'name': 'Buyer No Account', 'login': 'buyer_no_account', 'email': 'b@example.com',
            'group_ids': [(6, 0, [self.env.ref('base.group_user').id, self.env.ref('purchase.group_purchase_user').id])],
        })
        request = self._start().with_user(buyer)
        request.action_approve_and_send()
        self.assertFalse(self.env['evolution.message'].search([('res_model', '=', 'purchase.order')]))
        self.assertEqual(request.state, 'waiting')  # vendors with an email were reached

    # ------------------------------------------------------------ replies, quotes, comparison

    def test_quotes_comparison_and_deadline(self):
        request = self._start(needed_in_days=10)
        request.action_approve_and_send()
        self._flush_whatsapp()
        order_a = request.order_ids.filtered(lambda o: o.partner_id == self.vendor_whatsapp)
        order_b = request.order_ids.filtered(lambda o: o.partner_id == self.vendor_email)

        self.env['evolution.message']._register_incoming(self.account, '525511110001', "Son $100 c/u, entrega en 3 dias", external_id='R1')
        replies = json.loads(self.env['ai.buyer.request']._ai_get_request(request.id))['vendors_asked']
        reply_texts = [reply['text'] for row in replies for reply in row['replies']]
        self.assertTrue(any('$100' in text for text in reply_texts))

        Request = self.env['ai.buyer.request']
        Request._ai_register_quote(order_a.id, 100.0, 3, "Pago a 30 dias")
        comparison = json.loads(Request._ai_register_quote(order_b.id, 80.0, 20, None))
        ranking = comparison['ranking']
        self.assertEqual(ranking[0]['vendor'], 'Vendor A')  # on time beats cheaper but late
        self.assertEqual(ranking[1]['vendor'], 'Vendor B')
        self.assertTrue(ranking[1]['late'])
        self.assertIn('delivery after the needed date', ranking[1]['flags'])
        self.assertFalse(ranking[2]['quote_registered'])  # E never answered
        self.assertIn("Fewer than 3", comparison['warning'])
        self.assertEqual(order_a.order_line.price_unit, 100.0)
        self.assertEqual(order_a.ai_quote_notes, "Pago a 30 dias")

        with self.assertRaises(UserError):
            Request._ai_register_quote(order_a.id, -5.0)
        with self.assertRaises(UserError):
            Request._ai_register_quote(order_a.id, None)

        request.deadline_date = fields.Datetime.now() - timedelta(minutes=1)
        Request._cron_check_deadlines()
        self.assertEqual(request.state, 'ready')
        self.assertTrue(request.activity_ids)

        Request._ai_save_recommendation(request.id, "Elegir Vendor A: entrega a tiempo.")
        self.assertIn("Vendor A", request.recommendation)
        self.assertIn("Vendor A", request.comparison_html)

    def test_request_is_ready_as_soon_as_every_quote_is_in(self):
        request = self._start()
        request.action_approve_and_send()
        for order in request.order_ids:
            self.env['ai.buyer.request']._ai_register_quote(order.id, 90.0, 5)
        self.env['ai.buyer.request']._cron_check_deadlines()
        self.assertEqual(request.state, 'ready')

    def test_quote_cannot_be_registered_before_the_request_was_sent(self):
        request = self._start()
        with self.assertRaisesRegex(UserError, "not waiting"):
            self.env['ai.buyer.request']._ai_register_quote(request.order_ids[0].id, 10.0)

    def test_cancel_cancels_the_drafts(self):
        request = self._start()
        request.action_cancel()
        self.assertEqual(request.state, 'cancel')
        self.assertEqual(set(request.order_ids.mapped('state')), {'cancel'})

    # ------------------------------------------------------------ agent wiring

    def test_agent_has_the_buyer_tools_and_no_dangerous_ones(self):
        agent = self.env.ref('ai_buyer_agent.ai_agent_buyer')
        self.assertTrue(agent.system_prompt)
        tool_names = set(agent.sudo().topic_ids.tool_ids._get_ai_tools())
        self.assertLessEqual({'ai_buyer_start_request', 'ai_buyer_compare', 'ai_order_find_product'}, tool_names)
        for forbidden in ('send', 'approve', 'confirm', 'cancel', 'delete'):
            self.assertFalse([name for name in tool_names if forbidden in name], forbidden)

    def test_tool_runs_through_the_server_action(self):
        tool = self.env.ref('ai_buyer_agent.ai_buyer_start_request')
        answer = json.loads(tool._ai_tool_run(None, {
            'product_id': float(self.product.id), 'quantity': 25, 'needed_date': '2031-01-15',
        }))
        self.assertEqual(answer['state'], 'to_approve')
        self.assertEqual(len(answer['vendors_asked']), 3)
        self.assertIn('cannot send', answer['next_step'])

    def test_user_without_purchase_rights_cannot_use_the_tools(self):
        plain_user = self.env['res.users'].create({
            'name': 'No Purchase', 'login': 'no_purchase_buyer', 'group_ids': [(6, 0, [self.env.ref('base.group_user').id])],
        })
        with self.assertRaises(AccessError):
            self.env['ai.buyer.request'].with_user(plain_user)._ai_start_request(self.product.id, 5)

    def test_purchase_user_can_run_the_whole_flow(self):
        """A regular buyer (no admin rights) starts, approves, registers and compares."""
        buyer = self.env['res.users'].create({
            'name': 'Regular Buyer', 'login': 'regular_buyer_ai', 'email': 'buyer@example.com',
            'group_ids': [(6, 0, [self.env.ref('base.group_user').id, self.env.ref('purchase.group_purchase_user').id])],
        })
        Request = self.env['ai.buyer.request'].with_user(buyer)
        self.account.user_ids = buyer
        request = Request.browse(json.loads(Request._ai_start_request(self.product.id, 40, None, None))['id'])
        request.action_approve_and_send()
        self.assertEqual(request.state, 'waiting')
        for order in request.order_ids:
            comparison = json.loads(Request._ai_register_quote(order.id, 70.0, 4, "Contado"))
        self.assertEqual(comparison['quotes_registered'], 3)
        Request._cron_check_deadlines()
        self.assertEqual(request.sudo().state, 'ready')
        Request._ai_save_recommendation(request.id, "Todos al mismo precio; elegir el de mayor historial.")
