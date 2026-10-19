# -*- coding: utf-8 -*-
from odoo import _, api, fields, models
from odoo.exceptions import UserError

OPTIMIZABLE_STATES = ('requested', 'assigned')


class LogisticsRouteOptimizeWizard(models.TransientModel):
    _name = 'logistics.route.optimize.wizard'
    _description = 'Optimize Logistics Routes'

    route_date = fields.Date(string='Route Date', required=True, default=fields.Date.context_today)
    driver_employee_ids = fields.Many2many(
        'hr.employee', string='Drivers', required=True,
        domain="[('is_logistics_driver', '=', True)]",
        default=lambda self: self._default_driver_employee_ids())
    keep_assignment = fields.Boolean(
        string='Keep Current Drivers', default=False,
        help='Only reorder: stops that already have a driver stay with that driver.')
    confirm_assignment = fields.Boolean(
        string='Assign Stops After Optimizing', default=True,
        help='Move the planned stops from "Requested" to "Assigned".')
    route_ids = fields.Many2many('logistics.route', string='Stops', compute='_compute_route_ids')
    route_count = fields.Integer(compute='_compute_route_ids')
    routing_provider = fields.Char(compute='_compute_routing_provider')

    @api.model
    def _default_driver_employee_ids(self):
        driver_ids_with_vehicle = self.env['fleet.vehicle'].sudo().search(
            [('driver_employee_id.is_logistics_driver', '=', True)]).driver_employee_id.ids
        return [fields.Command.set(driver_ids_with_vehicle)]

    @api.depends('route_date')
    def _compute_route_ids(self):
        for wizard in self:
            wizard.route_ids = self.env['logistics.route'].search([
                ('date', '=', wizard.route_date),
                ('state', 'in', OPTIMIZABLE_STATES),
            ])
            wizard.route_count = len(wizard.route_ids)

    def _compute_routing_provider(self):
        routing_provider = self.env['ir.config_parameter'].sudo().get_param(
            'arbz_delivery_route.routing_provider', 'vroom')
        for wizard in self:
            wizard.routing_provider = 'Google' if routing_provider == 'google' else 'VROOM / OpenRouteService'

    def action_optimize(self):
        self.ensure_one()
        self.env['logistics.route']._check_supervisor_access()
        routes = self.route_ids
        if self.keep_assignment:
            routes = routes.filtered(
                lambda route: not route.driver_employee_id or route.driver_employee_id in self.driver_employee_ids)
        if not routes:
            raise UserError(_('There are no requested or assigned stops on %s.', self.route_date))
        planned_routes, unplanned_routes = self.env['logistics.route.optimizer']._optimize(
            routes, self.driver_employee_ids, self.route_date, self.keep_assignment)
        if self.confirm_assignment:
            planned_routes.filtered(lambda route: route.state == 'requested').action_assign()

        message = _('%(planned)s stops planned for %(drivers)s drivers.',
                    planned=len(planned_routes), drivers=len(planned_routes.driver_employee_id))
        if unplanned_routes:
            message += ' ' + _('Not planned (no coordinates, closed during the shift or no capacity): %s',
                               ', '.join(unplanned_routes.mapped('name')))
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _('Routes optimized'),
                'message': message,
                'type': 'warning' if unplanned_routes else 'success',
                'sticky': bool(unplanned_routes),
                'next': {
                    'type': 'ir.actions.act_window',
                    'name': _('Optimized Routes'),
                    'res_model': 'logistics.route',
                    'view_mode': 'map,list,form',
                    'views': [(False, 'map'), (False, 'list'), (False, 'form')],
                    'domain': [('id', 'in', planned_routes.ids)],
                    'context': {'search_default_group_driver': 1},
                },
            },
        }
