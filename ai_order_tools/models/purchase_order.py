from odoo import _, api, models
from odoo.exceptions import UserError

from ..utils.order_helpers import (
    browse_existing,
    check_order_is_editable,
    check_price,
    check_quantity,
    dump_json,
    get_record_url,
    parse_date,
    text_to_html,
)


class PurchaseOrder(models.Model):
    _inherit = 'purchase.order'

    def _ai_order_summary(self):
        self.ensure_one()
        return {
            'id': self.id,
            'name': self.name,
            'state': self.state,
            'vendor': self.partner_id.display_name,
            'vendor_reference': self.partner_ref or None,
            'expected_date': self.date_planned or None,
            'currency': self.currency_id.name,
            'amount_untaxed': self.amount_untaxed,
            'amount_total': self.amount_total,
            'url': get_record_url(self),
            'lines': [{
                'line_id': line.id,
                'product_id': line.product_id.id,
                'product': line.product_id.display_name,
                'quantity': line.product_qty,
                'unit_of_measure': line.product_uom_id.name,
                'price_unit': line.price_unit,
                'subtotal': line.price_subtotal,
            } for line in self.order_line.filtered(lambda line: not line.display_type)],
        }

    def _ai_apply_header(self, vendor_reference=None, note=None, expected_date=None):
        """Header values shared by the create and update tools; only given values are written."""
        header_values = {}
        if vendor_reference is not None:
            header_values['partner_ref'] = vendor_reference
        if note is not None:
            header_values['note'] = text_to_html(note)
        if expected_date is not None:
            header_values['date_planned'] = parse_date(expected_date, _("The expected date"))
        return header_values

    @api.model
    def _ai_create_rfq(self, partner_id, vendor_reference=None, note=None, expected_date=None):
        vendor = browse_existing(self.env['res.partner'], partner_id, _("Vendor"))
        order = self.create({
            'partner_id': vendor.id,
            **self._ai_apply_header(vendor_reference, note, expected_date),
        })
        return dump_json(order._ai_order_summary())

    @api.model
    def _ai_get_purchase_order(self, order_id):
        order = browse_existing(self, order_id, _("Purchase order"))
        return dump_json(order._ai_order_summary())

    @api.model
    def _ai_update_rfq(self, order_id, vendor_reference=None, note=None, expected_date=None):
        order = browse_existing(self, order_id, _("Request for quotation"))
        check_order_is_editable(order)
        header_values = self._ai_apply_header(vendor_reference, note, expected_date)
        if not header_values:
            raise UserError(_("Nothing to update: provide a reference, a note or an expected date."))
        order.write(header_values)
        return dump_json(order._ai_order_summary())

    @api.model
    def _ai_add_purchase_line(self, order_id, product_id, quantity, price_unit=None):
        order = browse_existing(self, order_id, _("Request for quotation"))
        check_order_is_editable(order)
        product = browse_existing(self.env['product.product'], product_id, _("Product"))
        if not product.purchase_ok:
            raise UserError(_("%s cannot be purchased.", product.display_name))
        line_values = {
            'order_id': order.id,
            'product_id': product.id,
            'product_qty': check_quantity(quantity),
        }
        if price_unit is not None:
            line_values['price_unit'] = check_price(price_unit)
        self.env['purchase.order.line'].create(line_values)
        return dump_json(order._ai_order_summary())

    @api.model
    def _ai_update_purchase_line(self, line_id, quantity=None, price_unit=None):
        line = browse_existing(self.env['purchase.order.line'], line_id, _("Order line"))
        if line.display_type:
            raise UserError(_("Section and note lines cannot be changed with this tool."))
        check_order_is_editable(line.order_id)
        line_values = {}
        if quantity is not None:
            line_values['product_qty'] = check_quantity(quantity)
        if price_unit is not None:
            line_values['price_unit'] = check_price(price_unit)
        if not line_values:
            raise UserError(_("Nothing to update: provide a quantity or a unit price."))
        line.write(line_values)
        return dump_json(line.order_id._ai_order_summary())
