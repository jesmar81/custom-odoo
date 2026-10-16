# Copyright © 2021 Garazd Creation (https://garazd.biz)
# @author: Yurii Razumovskyi (support@garazd.biz)
# @author: Iryna Razumovska (support@garazd.biz)
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl-3.0.html).

{
    'name': 'Odoo Facebook Domain Verification',
    'version': '19.0.1.0.0',
    'category': 'Website',
    'author': 'Garazd Creation',
    'website': 'https://garazd.biz/shop',
    'license': 'LGPL-3',
    'summary': 'Facebook | Meta Domain Verification by meta-tag',
    'images': ['static/description/banner.png', 'static/description/icon.png'],
    'depends': [
        'website',
    ],
    'data': [
        'views/website_templates.xml',
        'views/website_views.xml',
    ],
    'support': 'support@garazd.biz',
    'application': False,
    'installable': True,
    'auto_install': False,
}
