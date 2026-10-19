import json

from odoo.exceptions import UserError, ValidationError
from odoo.tests import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestOrderTools(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.customer = cls.env['res.partner'].create({'name': 'Zeta Customer SA', 'email': 'zeta@example.com'})
        cls.vendor = cls.env['res.partner'].create({'name': 'Omega Vendor SA', 'supplier_rank': 1})
        cls.product = cls.env['product.product'].create({
            'name': 'Widget Aurora', 'default_code': 'WID-AUR', 'list_price': 100.0, 'standard_price': 40.0,
            'sale_ok': True, 'purchase_ok': True,
        })
        cls.not_for_sale = cls.env['product.product'].create({'name': 'Internal Gizmo', 'sale_ok': False})

    def _run_tool(self, xml_id, arguments):
        """Execute a tool the way an agent does: JSON-like arguments validated by the schema."""
        tool = self.env.ref(f'ai_order_tools.{xml_id}')
        return json.loads(tool._ai_tool_run(None, arguments))

    def test_tools_are_exposed_to_the_default_agent(self):
        agent = self.env.ref('ai.ai_default_agent')
        tool_names = set(agent.sudo().topic_ids.tool_ids._get_ai_tools())
        self.assertLessEqual(
            {'ai_sale_create_quotation', 'ai_purchase_create_rfq', 'ai_order_find_product', 'ir_actions_server_search'},
            tool_names,
        )
        for forbidden in ('confirm', 'cancel', 'delete', 'unlink'):
            self.assertFalse([name for name in tool_names if forbidden in name], forbidden)

    def test_default_prompt_is_loaded_only_when_empty(self):
        agent = self.env.ref('ai.ai_default_agent')
        agent.system_prompt = False
        self.env['ai.agent']._ai_order_tools_load_default_prompt()
        self.assertIn("Asistente de Odoo", agent.system_prompt)
        agent.system_prompt = "Custom prompt written by a person"
        self.env['ai.agent']._ai_order_tools_load_default_prompt()
        self.assertEqual(agent.system_prompt, "Custom prompt written by a person")

    def test_find_partner_and_product(self):
        found = self._run_tool('ai_order_find_partner', {'search_text': 'zeta', 'role': 'customer'})
        self.assertEqual([partner['id'] for partner in found['partners']], [self.customer.id])
        found = self._run_tool('ai_order_find_product', {'search_text': 'WID-AUR', 'purpose': 'sale'})
        self.assertEqual(found['products'][0]['unit_price'], 100.0)
        self.assertNotIn('unit_cost', found['products'][0])
        found = self._run_tool('ai_order_find_product', {'search_text': 'Gizmo', 'purpose': 'sale'})
        self.assertEqual(found['products'], [])

    def test_sale_quotation_lifecycle(self):
        # ids arrive as floats from some LLMs
        order = self._run_tool('ai_sale_create_quotation', {
            'customer_id': float(self.customer.id), 'customer_reference': 'PO-77', 'validity_date': '2030-01-31',
        })
        self.assertEqual(order['state'], 'draft')
        self.assertEqual(order['customer_reference'], 'PO-77')
        order = self._run_tool('ai_sale_add_line', {
            'order_id': order['id'], 'product_id': self.product.id, 'quantity': 3,
        })
        self.assertEqual(order['amount_untaxed'], 300.0)
        line_id = order['lines'][0]['line_id']
        order = self._run_tool('ai_sale_update_line', {'line_id': line_id, 'quantity': 5, 'discount_percent': 10})
        self.assertEqual(order['amount_untaxed'], 450.0)
        order = self._run_tool('ai_sale_update_quotation', {'order_id': order['id'], 'note': 'Entrega <b>urgente</b>'})
        sale_order = self.env['sale.order'].browse(order['id'])
        self.assertIn('Entrega', sale_order.note)

    def test_confirmed_sale_order_cannot_be_changed(self):
        sale_order = self.env['sale.order'].create({
            'partner_id': self.customer.id,
            'order_line': [(0, 0, {'product_id': self.product.id, 'product_uom_qty': 1})],
        })
        sale_order.action_confirm()
        with self.assertRaisesRegex(UserError, "not a draft"):
            sale_order._ai_add_sale_line(sale_order.id, self.product.id, 1)
        with self.assertRaisesRegex(UserError, "not a draft"):
            sale_order._ai_update_sale_line(sale_order.order_line.id, quantity=9)
        with self.assertRaisesRegex(UserError, "not a draft"):
            sale_order._ai_update_quotation(sale_order.id, note='x')
        self.assertEqual(sale_order.order_line.product_uom_qty, 1)

    def test_sale_input_validation(self):
        order = self.env['sale.order']._ai_create_quotation(self.customer.id)
        order_id = json.loads(order)['id']
        sale_order = self.env['sale.order']
        for bad_call in (
            lambda: sale_order._ai_add_sale_line(order_id, self.product.id, 0),
            lambda: sale_order._ai_add_sale_line(order_id, self.product.id, -2),
            lambda: sale_order._ai_add_sale_line(order_id, self.product.id, 1, price_unit=-1),
            lambda: sale_order._ai_add_sale_line(order_id, self.product.id, 1, discount_percent=150),
            lambda: sale_order._ai_add_sale_line(order_id, self.not_for_sale.id, 1),
            lambda: sale_order._ai_add_sale_line(order_id, 999999, 1),
            lambda: sale_order._ai_create_quotation(999999),
            lambda: sale_order._ai_create_quotation(self.customer.id, validity_date='31/01/2030'),
            lambda: sale_order._ai_update_quotation(order_id),
        ):
            with self.assertRaises(UserError):
                bad_call()
        with self.assertRaises(ValidationError):  # schema: required argument missing
            self._run_tool('ai_sale_add_line', {'order_id': order_id, 'product_id': self.product.id})

    def test_purchase_rfq_lifecycle(self):
        order = self._run_tool('ai_purchase_create_rfq', {
            'vendor_id': self.vendor.id, 'vendor_reference': 'COT-9', 'expected_date': '2030-02-15',
        })
        self.assertEqual(order['state'], 'draft')
        self.assertTrue(order['expected_date'].startswith('2030-02-15'))
        order = self._run_tool('ai_purchase_add_line', {
            'order_id': order['id'], 'product_id': self.product.id, 'quantity': 10, 'price_unit': 35.0,
        })
        self.assertEqual(order['amount_untaxed'], 350.0)
        order = self._run_tool('ai_purchase_update_line', {'line_id': order['lines'][0]['line_id'], 'quantity': 20})
        self.assertEqual(order['amount_untaxed'], 700.0)
        order = self._run_tool('ai_purchase_update_rfq', {'order_id': order['id'], 'note': 'Urgente'})
        self.assertEqual(order['vendor_reference'], 'COT-9')
        self.assertIn('Urgente', self.env['purchase.order'].browse(order['id']).note)

    def test_confirmed_purchase_order_cannot_be_changed(self):
        purchase_order = self.env['purchase.order'].create({
            'partner_id': self.vendor.id,
            'order_line': [(0, 0, {'product_id': self.product.id, 'product_qty': 1, 'price_unit': 10})],
        })
        purchase_order.button_confirm()
        with self.assertRaisesRegex(UserError, "not a draft"):
            purchase_order._ai_add_purchase_line(purchase_order.id, self.product.id, 1)
        with self.assertRaisesRegex(UserError, "not a draft"):
            purchase_order._ai_update_purchase_line(purchase_order.order_line.id, quantity=9)

    def test_tools_respect_access_rights(self):
        """A user without sales access cannot create quotations through the tool."""
        restricted_user = self.env['res.users'].create({
            'name': 'No Sales', 'login': 'no_sales_ai', 'group_ids': [(6, 0, [self.env.ref('base.group_user').id])],
        })
        tool = self.env.ref('ai_order_tools.ai_sale_create_quotation').with_user(restricted_user)
        with self.assertRaises(Exception):
            tool._ai_tool_run(None, {'customer_id': self.customer.id})
