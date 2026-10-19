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

import math
from urllib.parse import urlencode

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError

EARTH_RADIUS_METERS = 6371000

# Single source of truth for the route lifecycle, shared by the back-office
# buttons and the mobile API.
STATE_TRANSITIONS = {
    'draft': {'requested', 'cancelled'},
    'requested': {'assigned', 'cancelled'},
    'assigned': {'on_route', 'requested', 'cancelled'},
    'on_route': {'delivered', 'postponed'},
    'postponed': {'requested', 'cancelled'},
    'cancelled': {'draft'},
    'delivered': set(),
}

OPEN_STATES = ('draft', 'requested', 'assigned', 'on_route')


def haversine_distance_meters(latitude_from, longitude_from, latitude_to, longitude_to):
    latitude_from_rad, latitude_to_rad = math.radians(latitude_from), math.radians(latitude_to)
    delta_latitude = latitude_to_rad - latitude_from_rad
    delta_longitude = math.radians(longitude_to - longitude_from)
    haversine = (
        math.sin(delta_latitude / 2) ** 2
        + math.cos(latitude_from_rad) * math.cos(latitude_to_rad) * math.sin(delta_longitude / 2) ** 2
    )
    return 2 * EARTH_RADIUS_METERS * math.asin(math.sqrt(haversine))


class LogisticsRoute(models.Model):
    _name = 'logistics.route'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _description = 'Logistics Route Stop'
    _order = 'date desc, driver_employee_id, sequence, id'
    _check_company_auto = True

    _driver_date_idx = models.Index('(driver_user_id, date)')

    name = fields.Char(required=True, copy=False, readonly=True, index=True, default=lambda self: _('New'))
    sequence = fields.Integer(default=10, help='Order of the stop within the driver\'s day.')
    company_id = fields.Many2one('res.company', required=True, index=True, default=lambda self: self.env.company)
    date = fields.Date(string='Route Date', required=True, default=fields.Date.context_today, tracking=True)
    start_time = fields.Float(required=True, tracking=True)
    end_time = fields.Float(required=True, tracking=True)
    lunch_start_time = fields.Float(tracking=True)
    lunch_end_time = fields.Float(tracking=True)
    customer_id = fields.Many2one('res.partner', string='Customer', required=True, tracking=True)
    branch_id = fields.Many2one('res.partner', string='Branch', required=True, tracking=True)
    attendant_id = fields.Many2one('res.partner', string='Attendant', required=True, tracking=True)
    courier_service_id = fields.Many2one(
        'res.partner', string='Courier Service', domain=[('its_courier_service', '=', True)], tracking=True)
    payment_method = fields.Selection([
        ('cash', 'Cash'),
        ('credit_card', 'Credit Card'),
        ('debit_card', 'Debit Card'),
        ('eft', 'Electronic Funds Transfer'),
        ('check', 'Check'),
    ], required=True, tracking=True)
    payment_type = fields.Selection([
        ('installment or deferred', 'Installment or deferred'),
        ('single', 'Single Payment'),
    ], required=True, tracking=True)
    service_type = fields.Selection([
        ('direct', 'Direct'),
        ('occur_paid', 'Occur Paid'),
        ('occur_unpaid', 'Occur Unpaid'),
        ('home_paid', 'Home Paid'),
        ('home_unpaid', 'Home Unpaid'),
        ('collection_paid', 'Collection Paid'),
        ('collection_unpaid', 'Collection Unpaid'),
    ], required=True, tracking=True)
    sale_order_ids = fields.Many2many('sale.order', string='Sale Orders', check_company=True, tracking=True)
    purchase_order_ids = fields.Many2many(
        'purchase.order', string='Purchase Orders', check_company=True, tracking=True)
    driver_employee_id = fields.Many2one(
        'hr.employee', string='Driver', domain=[('is_logistics_driver', '=', True)],
        check_company=True, index=True, tracking=True)
    driver_user_id = fields.Many2one(
        related='driver_employee_id.user_id', string='Driver User', store=True, index=True)
    vehicle_id = fields.Many2one('fleet.vehicle', string='Vehicle Assigned', check_company=True, tracking=True)
    observation = fields.Html()
    state = fields.Selection([
        ('draft', 'Draft'),
        ('requested', 'Requested'),
        ('assigned', 'Assigned'),
        ('on_route', 'On Route'),
        ('delivered', 'Delivered'),
        ('postponed', 'Postponed'),
        ('cancelled', 'Cancelled'),
    ], string='Status', group_expand='_group_expand_states', default='draft', readonly=True, index=True,
        tracking=True)

    # Location of the stop
    branch_latitude = fields.Float(related='branch_id.partner_latitude', string='Branch Latitude')
    branch_longitude = fields.Float(related='branch_id.partner_longitude', string='Branch Longitude')
    navigation_url = fields.Char(compute='_compute_navigation_url')

    # Execution timestamps (set by the driver app or the back-office buttons)
    planned_arrival = fields.Datetime(tracking=True)
    started_at = fields.Datetime(string='Started At', readonly=True, copy=False)
    arrived_at = fields.Datetime(string='Arrived At', readonly=True, copy=False)
    delivered_at = fields.Datetime(string='Delivered At', readonly=True, copy=False)

    # Proof of delivery
    received_by_name = fields.Char(string='Received By', copy=False, tracking=True)
    delivery_signature = fields.Binary(string='Signature', attachment=True, copy=False)
    delivery_photo_ids = fields.Many2many(
        'ir.attachment', 'logistics_route_delivery_photo_rel', 'route_id', 'attachment_id',
        string='Delivery Photos', copy=False)
    delivery_latitude = fields.Float(digits=(10, 7), copy=False, readonly=True)
    delivery_longitude = fields.Float(digits=(10, 7), copy=False, readonly=True)
    geofence_distance_m = fields.Float(
        string='Distance to Branch (m)', compute='_compute_geofence', store=True, digits=(16, 0))
    geofence_exceeded = fields.Boolean(compute='_compute_geofence', store=True)
    failure_reason = fields.Selection([
        ('customer_absent', 'Customer absent'),
        ('address_not_found', 'Address not found'),
        ('refused', 'Refused by customer'),
        ('closed', 'Branch closed'),
        ('damaged', 'Damaged goods'),
        ('other', 'Other'),
    ], copy=False, tracking=True)
    failure_note = fields.Text(copy=False)
    event_ids = fields.One2many('logistics.route.event', 'route_id', string='Events')

    def _group_expand_states(self, states, domain):
        return [state_key for state_key, _label in type(self).state.selection]

    @api.depends('branch_id', 'branch_latitude', 'branch_longitude')
    def _compute_navigation_url(self):
        for route in self:
            if route.branch_latitude or route.branch_longitude:
                destination = f'{route.branch_latitude},{route.branch_longitude}'
            else:
                destination = route.branch_id.contact_address_inline or ''
            route.navigation_url = destination and 'https://www.google.com/maps/dir/?' + urlencode(
                {'api': 1, 'destination': destination})

    @api.depends('delivery_latitude', 'delivery_longitude', 'branch_id.partner_latitude',
                 'branch_id.partner_longitude')
    def _compute_geofence(self):
        radius_meters = self._get_geofence_radius_meters()
        for route in self:
            has_delivery_point = route.delivery_latitude or route.delivery_longitude
            has_branch_point = route.branch_latitude or route.branch_longitude
            if has_delivery_point and has_branch_point:
                route.geofence_distance_m = haversine_distance_meters(
                    route.delivery_latitude, route.delivery_longitude,
                    route.branch_latitude, route.branch_longitude)
                route.geofence_exceeded = bool(radius_meters) and route.geofence_distance_m > radius_meters
            else:
                route.geofence_distance_m = 0
                route.geofence_exceeded = False

    @api.model
    def _get_geofence_radius_meters(self):
        return int(self.env['ir.config_parameter'].sudo().get_param('arbz_delivery_route.geofence_radius_m', 300))

    @api.onchange('driver_employee_id')
    def _onchange_driver_employee_id(self):
        if self.driver_employee_id and not self.vehicle_id:
            self.vehicle_id = self.env['fleet.vehicle'].search(
                [('driver_employee_id', '=', self.driver_employee_id.id)], limit=1)

    @api.model_create_multi
    def create(self, vals_list):
        for route_vals in vals_list:
            if route_vals.get('name', _('New')) == _('New'):
                route_vals['name'] = self.env['ir.sequence'].next_by_code('logistics.logistics') or _('New')
        return super().create(vals_list)

    @api.constrains('end_time', 'start_time', 'lunch_end_time', 'lunch_start_time')
    def _check_time(self):
        for route in self:
            if route.end_time <= route.start_time:
                raise ValidationError(_('End time should be greater than start time!'))
            if route.lunch_end_time and route.lunch_start_time and route.lunch_end_time <= route.lunch_start_time:
                raise ValidationError(_('Lunch end time should be greater than lunch start time!'))

    # ------------------------------------------------------------------
    # State machine
    # ------------------------------------------------------------------

    def _is_logistics_supervisor(self):
        return self.env.user.has_group('arbz_delivery_route.group_logistics_supervisor')

    def _check_operator_access(self):
        """Supervisors operate any stop; drivers only the stops assigned to them."""
        if self.env.su or self._is_logistics_supervisor():
            return
        foreign_routes = self.filtered(lambda route: route.driver_user_id != self.env.user)
        if foreign_routes or not self.env.user.has_group('arbz_delivery_route.group_logistics_driver'):
            raise AccessError(_('You can only operate the stops assigned to you.'))

    def _check_supervisor_access(self):
        if not (self.env.su or self._is_logistics_supervisor()):
            raise AccessError(_('Only logistics supervisors can perform this action.'))

    def _set_state(self, new_state, extra_values=None):
        state_labels = dict(self._fields['state']._description_selection(self.env))
        for route in self:
            if new_state not in STATE_TRANSITIONS[route.state]:
                raise UserError(_(
                    'Stop %(route)s cannot go from "%(current)s" to "%(target)s".',
                    route=route.name, current=state_labels[route.state], target=state_labels[new_state]))
        # Drivers only have read access: writes go through sudo once the
        # caller has been checked by the action method.
        self.sudo().write({'state': new_state, **(extra_values or {})})
        return True

    def action_request(self):
        if not self.env.user.has_group('arbz_delivery_route.group_logistics_user'):
            raise AccessError(_('Only logistics users can confirm stops.'))
        return self._set_state('requested')

    def action_assign(self):
        self._check_supervisor_access()
        unassigned_routes = self.filtered(lambda route: not route.driver_employee_id or not route.vehicle_id)
        if unassigned_routes:
            raise UserError(_('Set a driver and a vehicle before assigning: %s',
                              ', '.join(unassigned_routes.mapped('name'))))
        return self._set_state('assigned')

    def action_unassign(self):
        self._check_supervisor_access()
        return self._set_state('requested')

    def action_start(self, event_datetime=None):
        self._check_operator_access()
        return self._set_state('on_route', {'started_at': event_datetime or fields.Datetime.now()})

    def action_mark_arrived(self, event_datetime=None):
        self._check_operator_access()
        if self.filtered(lambda route: route.state != 'on_route'):
            raise UserError(_('Only stops on route can be marked as arrived.'))
        self.sudo().write({'arrived_at': event_datetime or fields.Datetime.now()})
        return True

    def action_deliver(self, event_datetime=None):
        self._check_operator_access()
        proof_required = self.env['ir.config_parameter'].sudo().get_param(
            'arbz_delivery_route.require_proof_of_delivery')
        if proof_required:
            routes_without_proof = self.filtered(
                lambda route: not route.delivery_signature and not route.delivery_photo_ids)
            if routes_without_proof:
                raise UserError(_('A signature or a photo is required to deliver: %s',
                                  ', '.join(routes_without_proof.mapped('name'))))
        return self._set_state('delivered', {'delivered_at': event_datetime or fields.Datetime.now()})

    def action_postpone(self):
        self._check_operator_access()
        if self.filtered(lambda route: not route.failure_reason):
            raise UserError(_('Set a failure reason before postponing the stop.'))
        return self._set_state('postponed')

    def action_reschedule(self):
        self._check_supervisor_access()
        return self._set_state('requested', {
            'failure_reason': False,
            'failure_note': False,
            'started_at': False,
            'arrived_at': False,
        })

    def action_cancel(self):
        self._check_supervisor_access()
        return self._set_state('cancelled')

    def action_reset_draft(self):
        if not self.env.user.has_group('arbz_delivery_route.group_logistics_manager'):
            raise AccessError(_('Only logistics managers can reopen cancelled stops.'))
        return self._set_state('draft')

    def action_geolocalize_branches(self):
        self.branch_id.geo_localize()
        return True

    def action_open_navigation(self):
        self.ensure_one()
        if not self.navigation_url:
            raise UserError(_('The branch has no address or coordinates.'))
        return {'type': 'ir.actions.act_url', 'url': self.navigation_url, 'target': 'new'}

    # ------------------------------------------------------------------
    # Mobile app events (online actions and offline sync share this path)
    # ------------------------------------------------------------------

    def _store_delivery_proof(self, proof):
        """Attach the proof sent by the app. ``proof`` is already validated by the controller."""
        self.ensure_one()
        self._check_operator_access()
        proof_values = {}
        if proof.get('received_by_name'):
            proof_values['received_by_name'] = proof['received_by_name']
        if proof.get('signature'):
            proof_values['delivery_signature'] = proof['signature']
        if proof.get('latitude') is not None and proof.get('longitude') is not None:
            proof_values['delivery_latitude'] = proof['latitude']
            proof_values['delivery_longitude'] = proof['longitude']
        if proof.get('photos'):
            photo_attachments = self.env['ir.attachment'].sudo().create([{
                'name': f'{self.name}-photo-{photo_index}.jpg',
                'datas': photo_base64,
                'res_model': self._name,
                'res_id': self.id,
                'mimetype': 'image/jpeg',
            } for photo_index, photo_base64 in enumerate(proof['photos'], start=1)])
            proof_values['delivery_photo_ids'] = [fields.Command.link(attachment.id) for attachment in
                                                  photo_attachments]
        if proof_values:
            self.sudo().write(proof_values)

    def _apply_driver_event(self, event):
        """Apply one event coming from the mobile app.

        ``event`` is a validated dict with keys: action, sync_uuid, event_datetime,
        latitude, longitude, note, failure_reason, received_by_name, signature, photos.
        Returns the created ``logistics.route.event`` (or the existing one when the
        ``sync_uuid`` was already processed, which makes offline retries idempotent).
        """
        self.ensure_one()
        self._check_operator_access()
        route_event_model = self.env['logistics.route.event'].sudo()
        if event.get('sync_uuid'):
            existing_event = route_event_model.search([('sync_uuid', '=', event['sync_uuid'])], limit=1)
            if existing_event:
                return existing_event

        action = event['action']
        event_datetime = event.get('event_datetime')
        if action == 'start':
            self.action_start(event_datetime)
        elif action == 'arrive':
            self.action_mark_arrived(event_datetime)
        elif action == 'deliver':
            self._store_delivery_proof(event)
            self.action_deliver(event_datetime)
        elif action == 'fail':
            self.sudo().write({
                'failure_reason': event.get('failure_reason') or 'other',
                'failure_note': event.get('note'),
            })
            self.action_postpone()
        elif action == 'note':
            if not event.get('note'):
                raise UserError(_('A note event needs a text.'))
        else:
            raise UserError(_('Unknown action %s.', action))

        if event.get('note'):
            self.sudo().message_post(body=event['note'], author_id=self.env.user.partner_id.id)

        return route_event_model.create({
            'route_id': self.id,
            'employee_id': self.env.user.employee_id.id,
            'event_type': action,
            'event_datetime': event_datetime or fields.Datetime.now(),
            'latitude': event.get('latitude') or 0.0,
            'longitude': event.get('longitude') or 0.0,
            'note': event.get('note'),
            'sync_uuid': event.get('sync_uuid'),
        })
