# -*- coding: utf-8 -*-

##############################################################################
#
#    Copyright (c) 2023 Jesus Ramirez (jesumarcos81@gmail.com)
#    You can modify it under the terms of the GNU LESSER
#    GENERAL PUBLIC LICENSE (LGPL v3), Version 3.
#
#    It is forbidden to publish, distribute, sublicense, or sell copies
#    of the Software or modified copies of the Software.
#
#    This program is distributed in the hope that it will be useful,
#    but WITHOUT ANY WARRANTY; without even the implied warranty of
#    MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#    GNU LESSER GENERAL PUBLIC LICENSE (LGPL v3) for more details.
#
#    You should have received a copy of the GNU LESSER GENERAL PUBLIC LICENSE
#    GENERAL PUBLIC LICENSE (LGPL v3) along with this program.
#    If not, see <http://www.gnu.org/licenses/>.
#
##############################################################################

from odoo import fields, models, api, _
from odoo.exceptions import ValidationError

class Logistics(models.Model):
    _name = 'logistics.route'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _description = 'Logistics'
    _order = 'name desc'
    _rec_name = 'name'

    #name = fields.Char(string='Name', required=True, readonly=True, default=lambda self: self.env['ir.sequence'].next_by_code('logistics.logistics'))
    name = fields.Char(string='Name', required=True, copy=False, readonly=True, index=True, default=lambda self:_('New'))
    date = fields.Date(string='Date', required=True, default=fields.Date.context_today, tracking=True)
    start_time = fields.Float(string='Start Time', required=True, tracking=True)
    end_time = fields.Float(string='End Time', required=True, tracking=True)
    lunch_start_time = fields.Float(string='Lunch Start Time', tracking=True)
    lunch_end_time = fields.Float(string='Lunch End Time', tracking=True)
    customer_id = fields.Many2one('res.partner', string='Customer', required=True, tracking=True)
    branch_id = fields.Many2one('res.partner', string='Branch', required=True, tracking=True)
    attendant  = fields.Many2one('res.partner', string='Attendant', required=True, tracking=True)
    courier_service_id = fields.Many2one('res.partner', string='Courier Service', domain=[('its_courier_service', '=', True,)], tracking=True)
    payment_method = fields.Selection([('cash', 'Cash'), ('credit_card', 'Credit Card'), ('debit_card', 'Debit Card'), ('eft', 'Electronic Funds Transfer'), ('check', 'Check')], string='Payment Method', required=True, tracking=True)
    payment_type = fields.Selection([('installment or deferred', 'Installment or deferred'), ('single', 'Single Payment')], string='Payment Type', required=True, tracking=True)
    service_type = fields.Selection([('direct', 'Direct'), ('occur_paid', 'Occur Paid'), ('occur_unpaid', 'Occur Unpaid'), ('home_paid', 'Home Paid'), ('home_unpaid', 'Home Unpaid'), ('collection_paid', 'Collection Paid'), ('collection_unpaid', 'Collection Unpaid')], string='Service Type', required=True, tracking=True)
    sale_order_id = fields.Many2many('sale.order', string='Sale Orders', tracking=True)
    purchase_order_id = fields.Many2many('purchase.order', string='Purchase Orders', tracking=True)
    driver_id = fields.Many2one(comodel_name='res.partner', string='Driver', domain=[('its_vehicle_drivers', '=', True)])
    vehicle_id = fields.Many2one(comodel_name='fleet.vehicle', string='Vehicle Assigned')
    observation = fields.Html(string='Observation')
    state = fields.Selection([('draft', 'Draft'), ('requested', 'Requested'), ('assigned', 'Assigned'), ('on_route', 'On Route'), ('delivered', 'Delivered'), ('postponed', 'Postponed'), ('cancelled', 'Cancelled')], string='Status', group_expand='_group_expand_states', default='draft', readonly=True, tracking=True)

    def _group_expand_states(self, states, domain):
        return [key for key, val in type(self).state.selection]

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('name', _('New')) == _('New'):
                vals['name'] = self.env['ir.sequence'].next_by_code('logistics.logistics') or _('New')
        return super().create(vals_list)

    @api.constrains('end_time', 'start_time', 'lunch_end_time', 'lunch_start_time')
    def _check_time(self):
        for record in self:
            if record.end_time <= record.start_time:
                raise ValidationError("End time should be greater than start time!")
            if record.lunch_end_time and record.lunch_start_time:
                if record.lunch_end_time <= record.lunch_start_time:
                    raise ValidationError("Lunch end time should be greater than lunch start time!")

    def next_status(self):
        for record in self:
            if record.state == 'draft':
                record.state = 'requested'
            elif record.state == 'requested':
                record.state = 'assigned'
            elif record.state == 'assigned':
                record.state = 'on_route'
            elif record.state == 'on_route':
                record.state = 'delivered'
            elif record.state == 'delivered':
                record.state = 'postponed'
            elif record.state in ['postponed', 'cancelled']:
                record.state = 'draft'

    def action_cancel(self):
        for record in self:
            if record.state != 'draft':
                record.state = 'cancelled'

    def action_postpone(self):
        for record in self:
            if record.state == 'on_route':
                record.state = 'postponed'

    def action_in_progress(self):
        self.ensure_one()
        for record in self:
            if record.state == 'draft':
                self.write({'state': 'requested'})

    def action_in_postponed(self):
        self.ensure_one()
        for record in self:
            if record.state == 'postponed':
                self.write({'state': 'requested'})
