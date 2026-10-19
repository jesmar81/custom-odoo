{
    'name': 'AI Order Tools',
    'version': '19.0.1.0.0',
    'category': 'Hidden',
    'summary': "AI tools to create and edit draft sales quotations and purchase RFQs",
    'description': """
Tools for the Odoo AI agents (usable with any provider, including the custom connections):

* find customers, vendors and products,
* create a draft quotation / request for quotation,
* add lines, change quantities, prices and discounts,
* update the reference, notes and dates.

Confirming, cancelling and deleting orders are deliberately NOT available: a human does it.
Every tool runs with the access rights of the user talking to the agent.
""",
    'author': 'jesmar81',
    'depends': ['ai_app', 'sale', 'purchase'],
    'data': [
        'data/ir_actions_server_tools.xml',
        'data/ai_topic.xml',
        'data/ai_agent_data.xml',
    ],
    'license': 'LGPL-3',
}
