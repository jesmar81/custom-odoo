# -*- coding: utf-8 -*-
{
    'name': 'Logistics',
    'summary': 'Module for managing logistics routes',
    'description': 'Module for managing logistics routes',
    'author': 'Jesus Ramirez',
    'website': 'https://airibuz.com',
    'category': 'Operations/Inventory',
    'version': '19.0.1.0.1',
    'depends': ['base','sale','purchase'],
    'data': [
        'security/security.xml',
        'data/logistics_sequence.xml',
        'views/res_partner_views.xml',
        'views/logistics_route_views.xml',
        'security/ir.model.access.csv',
    ],
    'installable': True,
    'application': True,
    'auto_install': False,

}
