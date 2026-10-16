# -*- coding: utf-8 -*-

{
    'name': 'Report Invoice UUID',
    'version': '19.0.1.0.0',
    'category' : 'Accounting & Finance',
    'license': 'Other proprietary',
    'summary': """Report UUID Invoice MexicoT.""",
    'description': """
            Report UUID Invoice Mexico

    """,
    'author': "Jesus Ramirez",
    'website': "http://www.airibuz.com",
    'support': 'jramirez@airibuz.com',
    'depends': [
                'l10n_mx_edi',
                ],
    'data':[
        'views/l10n_mx_edi_report_invoice_airibuz.xml',
    ],
    'installable' : True,
    'application' : False,
}


