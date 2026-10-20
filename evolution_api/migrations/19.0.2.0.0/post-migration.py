import secrets

from odoo import SUPERUSER_ID, api


def migrate(cr, version):
    """Turn the single global configuration of 19.0.1 into the first WhatsApp account."""
    env = api.Environment(cr, SUPERUSER_ID, {})
    parameters = env['ir.config_parameter'].sudo()
    base_url = parameters.get_param('evolution_api.base_url')
    instance = parameters.get_param('evolution_api.instance')
    if not (base_url and instance) or env['evolution.account'].search_count([]):
        return
    account = env['evolution.account'].create({
        'name': "Principal",
        'base_url': base_url,
        'instance': instance,
        'api_key': parameters.get_param('evolution_api.api_key'),
        'webhook_token': parameters.get_param('evolution_api.webhook_token') or secrets.token_urlsafe(32),
        'default_country_code': parameters.get_param('evolution_api.default_country_code') or '52',
        'user_ids': [(6, 0, env.ref('base.group_user').user_ids.ids)],
        'active': parameters.get_param('evolution_api.enabled') in ('True', 'true', '1'),
    })
    cr.execute("UPDATE evolution_message SET account_id = %s WHERE account_id IS NULL", (account.id,))
