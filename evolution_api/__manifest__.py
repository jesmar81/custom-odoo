{
    'name': 'Evolution API (WhatsApp)',
    'version': '19.0.2.0.0',
    'category': 'Hidden/Tools',
    'summary': "Multi-account WhatsApp through your own Evolution API server: queue with sending limits, permissions, templates, files and replies",
    'description': """
Connector for Evolution API (v2) built for a low-volume business use, where losing a number is costly:

* Several accounts (numbers), each with its own server, permissions, daily limit, sending hours and spacing.
* Every message goes through a queue: random spacing, daily cap per account and per contact,
  known contacts only, opt-out handling, automatic pause of a failing account.
* Drafts proposed by people or AI agents wait for an explicit approval.
* Templates with document variables, PDFs of the documents and other files.
* Conversations with the replies received through a per-account webhook.
""",
    'author': 'jesmar81',
    'depends': ['mail', 'base_setup', 'bus'],
    'data': [
        'security/evolution_security.xml',
        'security/ir.model.access.csv',
        'data/ir_cron_data.xml',
        'views/evolution_account_views.xml',
        'views/evolution_message_views.xml',
        'views/evolution_conversation_views.xml',
        'views/evolution_template_views.xml',
        'views/evolution_optout_views.xml',
        'views/res_partner_views.xml',
        'views/res_config_settings_views.xml',
        'wizard/evolution_composer_views.xml',
        'views/evolution_menus.xml',
    ],
    'license': 'LGPL-3',
}
