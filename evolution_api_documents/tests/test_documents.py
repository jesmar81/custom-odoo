from odoo.tests import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestDocumentButtons(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.user = cls.env['res.users'].create({
            'name': 'Sender', 'login': 'sender_docs', 'email': 'sender@example.com',
            'group_ids': [(6, 0, [cls.env.ref('evolution_api.group_evolution_user').id,
                                  cls.env.ref('sales_team.group_sale_salesman').id,
                                  cls.env.ref('purchase.group_purchase_user').id,
                                  cls.env.ref('account.group_account_invoice').id])],
        })
        cls.customer = cls.env['res.partner'].create({'name': 'Cliente Docs', 'phone': '55 1111 2222'})
        cls.account = cls.env['evolution.account'].create({
            'name': 'Docs', 'base_url': 'https://evo.example.com', 'instance': 'docs', 'user_ids': [(6, 0, cls.user.ids)],
            'default_model_ids': [(6, 0, [cls.env['ir.model']._get(model).id for model in ('sale.order', 'purchase.order', 'account.move')])],
        })
        cls.product = cls.env['product.product'].create({'name': 'Producto Docs', 'list_price': 10, 'purchase_ok': True})

    def _documents(self):
        sale = self.env['sale.order'].create({
            'partner_id': self.customer.id, 'order_line': [(0, 0, {'product_id': self.product.id, 'product_uom_qty': 2})],
        })
        purchase = self.env['purchase.order'].create({
            'partner_id': self.customer.id, 'order_line': [(0, 0, {'product_id': self.product.id, 'product_qty': 2})],
        })
        invoice = self.env['account.move'].create({
            'move_type': 'out_invoice', 'partner_id': self.customer.id,
            'invoice_line_ids': [(0, 0, {'product_id': self.product.id, 'quantity': 1, 'price_unit': 10})],
        })
        return sale, purchase, invoice

    def test_button_opens_the_composer_for_each_document(self):
        for document in self._documents():
            context = document.with_user(self.user).action_send_whatsapp()['context']
            self.assertEqual((context['default_res_model'], context['default_res_id']), (document._name, document.id))
            self.assertEqual((context['default_partner_id'], context['default_phone']), (self.customer.id, self.customer.phone))
            self.assertEqual(context['default_account_id'], self.account.id, document._name)

    def test_views_show_the_button_only_to_whatsapp_users(self):
        for document in self._documents():
            arch = document.with_user(self.user).get_view(view_type='form')['arch']
            self.assertIn('action_send_whatsapp', arch)
            plain = self.env['res.users'].create({
                'name': 'Plain ' + document._name, 'login': 'plain_' + document._name.replace('.', '_'),
                'group_ids': [(6, 0, [self.env.ref('base.group_user').id, self.env.ref('sales_team.group_sale_salesman').id,
                                      self.env.ref('purchase.group_purchase_user').id, self.env.ref('account.group_account_invoice').id])],
            })
            # implied internal group only: the button is hidden by its group
            self.assertNotIn('action_send_whatsapp', document.with_user(plain).get_view(view_type='form')['arch'])

    def test_templates_render_and_carry_the_pdf(self):
        sale, purchase, invoice = self._documents()
        for document, xmlid in ((sale, 'sale_order'), (purchase, 'purchase_order'), (invoice, 'invoice')):
            template = self.env.ref(f'evolution_api_documents.evolution_template_{xmlid}')
            body = template.with_user(self.user)._render_body(document)
            self.assertIn('Cliente Docs', body)
            self.assertTrue(template.report_id)
