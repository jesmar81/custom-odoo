from odoo import _, api, models
from odoo.exceptions import UserError

from ..utils.order_helpers import (
    browse_existing,
    check_discount,
    check_order_is_editable,
    check_price,
    check_quantity,
    dump_json,
    get_record_url,
    parse_date,
    text_to_html,
)


class SaleOrder(models.Model):
    _inherit = 'sale.order'

    def _ai_order_summary(self):
        self.ensure_one()
        return {
            'id': self.id,
            'name': self.name,
            'state': self.state,
            'customer': self.partner_id.display_name,
            'customer_reference': self.client_order_ref or None,
            'validity_date': self.validity_date or None,
            'currency': self.currency_id.name,
            'amount_untaxed': self.amount_untaxed,
            'amount_total': self.amount_total,
            'url': get_record_url(self),
            'lines': [{
                'line_id': line.id,
                'product_id': line.product_id.id,
                'product': line.product_id.display_name,
                'quantity': line.product_uom_qty,
                'unit_of_measure': line.product_uom_id.name,
                'price_unit': line.price_unit,
                'discount_percent': line.discount,
                'subtotal': line.price_subtotal,
            } for line in self.order_line.filtered(lambda line: not line.display_type)],
        }

    def _ai_apply_header(self, customer_reference=None, note=None, validity_date=None):
        """Header values shared by the create and update tools; only given values are written."""
        header_values = {}
        if customer_reference is not None:
            header_values['client_order_ref'] = customer_reference
        if note is not None:
            header_values['note'] = text_to_html(note)
        if validity_date is not None:
            header_values['validity_date'] = parse_date(validity_date, _("The validity date"))
        return header_values

    @api.model
    def _ai_create_quotation(self, partner_id, customer_reference=None, note=None, validity_date=None):
        partner = browse_existing(self.env['res.partner'], partner_id, _("Customer"))
        order = self.create({
            'partner_id': partner.id,
            **self._ai_apply_header(customer_reference, note, validity_date),
        })
        return dump_json(order._ai_order_summary())

    @api.model
    def _ai_get_sale_order(self, order_id):
        order = browse_existing(self, order_id, _("Sales order"))
        return dump_json(order._ai_order_summary())

    @api.model
    def _ai_update_quotation(self, order_id, customer_reference=None, note=None, validity_date=None):
        order = browse_existing(self, order_id, _("Quotation"))
        check_order_is_editable(order)
        header_values = self._ai_apply_header(customer_reference, note, validity_date)
        if not header_values:
            raise UserError(_("Nothing to update: provide a reference, a note or a validity date."))
        order.write(header_values)
        return dump_json(order._ai_order_summary())

    @api.model
    def _ai_add_sale_line(self, order_id, product_id, quantity, price_unit=None, discount_percent=None):
        order = browse_existing(self, order_id, _("Quotation"))
        check_order_is_editable(order)
        product = browse_existing(self.env['product.product'], product_id, _("Product"))
        if not product.sale_ok:
            raise UserError(_("%s cannot be sold.", product.display_name))
        line_values = {
            'order_id': order.id,
            'product_id': product.id,
            'product_uom_qty': check_quantity(quantity),
        }
        if price_unit is not None:
            line_values['price_unit'] = check_price(price_unit)
        if discount_percent is not None:
            line_values['discount'] = check_discount(discount_percent)
        self.env['sale.order.line'].create(line_values)
        return dump_json(order._ai_order_summary())

    @api.model
    def _ai_update_sale_line(self, line_id, quantity=None, price_unit=None, discount_percent=None):
        line = browse_existing(self.env['sale.order.line'], line_id, _("Order line"))
        if line.display_type:
            raise UserError(_("Section and note lines cannot be changed with this tool."))
        check_order_is_editable(line.order_id)
        line_values = {}
        if quantity is not None:
            line_values['product_uom_qty'] = check_quantity(quantity)
        if price_unit is not None:
            line_values['price_unit'] = check_price(price_unit)
        if discount_percent is not None:
            line_values['discount'] = check_discount(discount_percent)
        if not line_values:
            raise UserError(_("Nothing to update: provide a quantity, a unit price or a discount."))
        line.write(line_values)
        return dump_json(line.order_id._ai_order_summary())
