{
    'name': 'Evolution API (WhatsApp)',
    'version': '19.0.1.0.0',
    'category': 'Hidden/Tools',
    'summary': "Send WhatsApp messages through your own Evolution API server and receive the replies in the chatter",
    'description': """
Connector for an Evolution API (v2) instance, an alternative to the Odoo WhatsApp (Meta Cloud API) module:

* Settings: server URL, instance, API key, default country code, webhook URL.
* Every message sent or received is logged (Settings > Technical > Evolution Messages).
* Replies reach Odoo through a webhook and are posted in the chatter of the document the
  conversation started from, or in the contact's chatter.
""",
    'author': 'jesmar81',
    'depends': ['mail', 'base_setup'],
    'data': [
        'security/ir.model.access.csv',
        'views/evolution_message_views.xml',
        'views/res_config_settings_views.xml',
        'data/evolution_api_data.xml',
    ],
    'license': 'LGPL-3',
}
