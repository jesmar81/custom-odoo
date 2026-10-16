# -*- coding: utf-8 -*-
{
    'name': 'Display Amount In Words in quotations and purchase orders',
    'version': '19.0.1.0.0',
    'author': 'Jesus Ramirez',
    'summary': 'Display Amount In Words in quotations and purchase orders, both in forms as well as reports',
    'description': """This module displays Total Amount In Words in quotations and purchase orders, both in forms as well as reports.""",
    'category': 'Sales',
    'website': 'https://www.airibuz.com/',
    'license': 'AGPL-3',
    'depends': ['sale_management','purchase'],
    'data': [
        'views/sale_order_view.xml',
        'views/purchase_order_view.xml',
        'report/sale_order_report.xml',
        'report/purchase_order_report.xml',
    ],
    'installable': True,
    'application': True,
    'auto_install': False,
}
