# -*- coding: utf-8 -*-
{
    'name': 'Cost OT',
    'summary': 'Module for managing cost OT',
    'description': 'Manage Operational Costs for Orders',
    'author': 'Jesus Ramirez',
    'website': 'https://airibuz.com',
    'category': 'Operations/Inventory',
    'version': '19.0.1.0.0',
    'depends': ['base', 'stock'],
    'data': [
        'security/security.xml',
        'security/ir.model.access.csv',
        'wizard/arbz_report_ot.xml',
    ],
    'post_init_hook': 'post_init_hook',
    'installable': True,
    'application': False,
    'auto_install': False,

}
