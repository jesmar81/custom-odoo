# -*- coding: utf-8 -*-
{
    'name': 'Disable remove SA from Partners Name on CFDI',

    'summary': """
        Short (1 phrase/line) summary of the module's purpose, used as
        subtitle on modules listing or apps.openerp.com""",

    'description': """This module disable the Odoo Enterprise CFDI Localization funcionality that s removes S.A. and S.A. de C.V. from name.

At this tiome some Partners has in its name Customer SA wich is his Legal name so we need to disable this funcionality.

""",

    'author': 'Real Systems', 'maintainer': 'Carlos Contreras <carlosecv@realsystems.com.mx>',
    'website': 'https://www.realsystems.com.mx',

    # Categories can be used to filter modules in modules listing
    # Check https://github.com/odoo/odoo/blob/15.0/odoo/addons/base/data/ir_module_category_data.xml
    # for the full list
    'category': 'Localization',
    'version': '19.0.1.0.0',

    # any module necessary for this one to work correctly
    'depends': ['base', 'l10n_mx_edi'],

    # always loaded
    'data': [
        # 'security/ir.model.access.csv',
        #'views/views.xml',
        #'views/templates.xml',
    ],
    # only loaded in demonstration mode
    'demo': [
        #'demo/demo.xml',
    ],
}
