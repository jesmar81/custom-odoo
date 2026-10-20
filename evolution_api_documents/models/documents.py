from odoo import models


class SaleOrder(models.Model):
    _name = 'sale.order'
    _inherit = ['sale.order', 'evolution.mixin']


class PurchaseOrder(models.Model):
    _name = 'purchase.order'
    _inherit = ['purchase.order', 'evolution.mixin']


class AccountMove(models.Model):
    _name = 'account.move'
    _inherit = ['account.move', 'evolution.mixin']
