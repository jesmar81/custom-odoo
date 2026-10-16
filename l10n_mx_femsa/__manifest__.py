# -*- coding: utf-8 -*-

# Part of Probuse Consulting Service Pvt Ltd. See LICENSE file for full copyright and licensing details.

{
    'name': 'Adenda Femsa',
    'version': '19.0.1.0.0',
    'category' : 'Accounting & Finance',
    'license': 'Other proprietary',
    'summary': """This module allow you to ​Adenda Femsa Autoparts into Odoo.""",
    'description': """
Data Required to generate the Adenda Femsa
Purchase order
Coin
Delivery order
Entry

    """,
    'author': "Jesus Ramirez",
    'website': "http://www.airibuz.com",
    'support': 'jramirez@airibuz.com',
    'depends': [
                'l10n_mx_edi',
                ],
    'data':[
        'views/account_move_view.xml',
        'views/l10n_mx_edi_addenda_femsa.xml',
    ],
    'installable' : True,
    'application' : False,
}

# vim:expandtab:smartindent:tabstop=4:softtabstop=4:shiftwidth=4:
