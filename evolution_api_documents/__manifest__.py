{
    'name': 'Evolution API: WhatsApp on documents',
    'version': '19.0.1.0.0',
    'category': 'Hidden/Tools',
    'summary': "WhatsApp button on sales orders, purchase orders and invoices, with ready-to-use templates",
    'author': 'jesmar81',
    'depends': ['evolution_api', 'sale', 'purchase', 'account'],
    'data': [
        'views/sale_order_views.xml',
        'views/purchase_order_views.xml',
        'views/account_move_views.xml',
        'data/evolution_template_data.xml',
    ],
    'auto_install': True,
    'license': 'LGPL-3',
}
