from odoo import api, models

from ..utils.order_helpers import MAX_SEARCH_RESULTS, dump_json


class ProductProduct(models.Model):
    _inherit = 'product.product'

    @api.model
    def _ai_find_products(self, search_text, purpose='sale'):
        """Find the products that can be sold ('sale') or bought ('purchase').

        The sales price is returned for sales and the cost for purchases, so a
        salesperson is not shown the company cost.
        """
        search_text = (search_text or '').strip()
        if len(search_text) < 2:
            return dump_json({'error': "Provide at least 2 characters to search a product."})
        is_purchase = purpose == 'purchase'
        matching_products = self.search(
            [
                ('purchase_ok' if is_purchase else 'sale_ok', '=', True),
                '|', '|',
                ('name', 'ilike', search_text),
                ('default_code', 'ilike', search_text),
                ('barcode', '=', search_text),
            ],
            limit=MAX_SEARCH_RESULTS,
        )
        return dump_json({
            'products': [{
                'id': product.id,
                'name': product.display_name,
                'internal_reference': product.default_code or None,
                'unit_of_measure': product.uom_id.name,
                'unit_cost' if is_purchase else 'unit_price': product.standard_price if is_purchase else product.lst_price,
            } for product in matching_products],
            'note': "No product found: ask the user to rephrase, do not invent an id."
                    if not matching_products else f"At most {MAX_SEARCH_RESULTS} results shown.",
        })
