{
    'name': 'AI Buyer Agent',
    'version': '19.0.1.1.0',
    'category': 'Inventory/Purchase',
    'summary': "AI buyer that asks 3 vendors from your purchase history for a quotation and compares the answers",
    'description': """
Buyer agent workflow (a person approves before anything is sent):

1. The agent receives "I need N units of X" and picks up to 3 vendors from the purchase history.
2. Vendors flagged "buy through an intermediary" are NEVER contacted or touched by the AI.
3. It prepares one draft request for quotation per vendor (linked as alternatives).
4. A person reviews and clicks "Approve and send": WhatsApp (Evolution API) or email.
5. Replies are waited for (24 h by default); then the agent registers the quotes and compares them.
6. The agent writes a recommendation; a person confirms the purchase.
""",
    'author': 'jesmar81',
    'depends': [
        'ai_order_tools', 'ai_custom_providers', 'evolution_api', 'purchase', 'purchase_requisition', 'mail',
    ],
    'data': [
        'security/ir.model.access.csv',
        'security/ai_buyer_security.xml',
        'data/ir_sequence_data.xml',
        'data/ir_cron_data.xml',
        'views/res_partner_views.xml',
        'views/purchase_order_views.xml',
        'views/ai_buyer_request_views.xml',
        'views/res_config_settings_views.xml',
        'data/ir_actions_server_tools.xml',
        'data/ai_topic.xml',
        'data/ai_agent_data.xml',
    ],
    'license': 'LGPL-3',
}
