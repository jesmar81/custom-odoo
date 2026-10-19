from odoo import api, models

from ..utils.order_helpers import MAX_SEARCH_RESULTS, dump_json


class ResPartner(models.Model):
    _inherit = 'res.partner'

    @api.model
    def _ai_find_partners(self, search_text, role=None):
        """Find partners by name, email, tax id or internal reference.

        :param role: 'customer' or 'vendor' only sorts the matching partners that
            already played that role first; nobody is filtered out.
        """
        search_text = (search_text or '').strip()
        if len(search_text) < 2:
            return dump_json({'error': "Provide at least 2 characters to search a partner."})
        matching_partners = self.search(
            [
                '|', '|', '|',
                ('complete_name', 'ilike', search_text),
                ('email', 'ilike', search_text),
                ('vat', 'ilike', search_text),
                ('ref', 'ilike', search_text),
            ],
            order={
                'customer': 'customer_rank desc, id',
                'vendor': 'supplier_rank desc, id',
            }.get(role, 'complete_name, id'),
            limit=MAX_SEARCH_RESULTS,
        )
        return dump_json({
            'partners': [{
                'id': partner.id,
                'name': partner.display_name,
                'email': partner.email or None,
                'phone': partner.phone or None,
                'city': partner.city or None,
                'country': partner.country_id.name or None,
            } for partner in matching_partners],
            'note': "No partner found: ask the user for more details, do not invent an id."
                    if not matching_partners else f"At most {MAX_SEARCH_RESULTS} results shown.",
        })
