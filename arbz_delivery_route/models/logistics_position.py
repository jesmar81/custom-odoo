# -*- coding: utf-8 -*-
from datetime import timedelta

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError


class LogisticsPosition(models.Model):
    """GPS breadcrumbs from the drivers' mobile app or from the vehicles' GPS trackers."""
    _name = 'logistics.position'
    _description = 'Logistics GPS Position'
    _order = 'recorded_at desc, id desc'

    _employee_recorded_idx = models.Index('(employee_id, recorded_at)')
    _vehicle_recorded_idx = models.Index('(vehicle_id, recorded_at)')

    source = fields.Selection([
        ('app', 'Mobile App'),
        ('device', 'GPS Tracker'),
    ], required=True, default='app', index=True)
    employee_id = fields.Many2one('hr.employee', string='Driver', ondelete='cascade')
    vehicle_id = fields.Many2one('fleet.vehicle', string='Vehicle', ondelete='cascade')
    company_id = fields.Many2one('res.company', required=True, index=True, default=lambda self: self.env.company)
    latitude = fields.Float(digits=(10, 7), required=True)
    longitude = fields.Float(digits=(10, 7), required=True)
    accuracy_m = fields.Float(string='Accuracy (m)')
    speed_kmh = fields.Float(string='Speed (km/h)')
    heading_deg = fields.Float(string='Heading (°)')
    ignition = fields.Boolean()
    recorded_at = fields.Datetime(required=True, default=fields.Datetime.now)

    @api.constrains('employee_id', 'vehicle_id')
    def _check_position_owner(self):
        if self.filtered(lambda position: not position.employee_id and not position.vehicle_id):
            raise ValidationError(_('A GPS position belongs to a driver or to a vehicle.'))

    @api.model
    def _record_app_positions(self, employee, position_values_list):
        """Store a batch of validated positions sent by the driver app."""
        if not position_values_list:
            return self.browse()
        vehicle = self.env['fleet.vehicle'].sudo().search([('driver_employee_id', '=', employee.id)], limit=1)
        positions = self.sudo().create([{
            **position_values,
            'source': 'app',
            'employee_id': employee.id,
            'company_id': employee.company_id.id,
        } for position_values in position_values_list])
        latest_position = max(positions, key=lambda position: position.recorded_at)
        employee.sudo()._update_live_position(latest_position)
        # The phone is a fallback marker for vehicles without a tracker.
        if vehicle and not vehicle.gps_device_identifier:
            vehicle._update_live_position(latest_position)
        return positions

    @api.model
    def _record_device_positions(self, position_values_list):
        """Store positions forwarded by the GPS platform (Traccar, vendor webhook...).

        Each item carries ``device_identifier`` (IMEI / unique id configured on the
        vehicle). Returns ``(positions, unknown_device_identifiers)``.
        """
        device_identifiers = {position_values['device_identifier'] for position_values in position_values_list}
        vehicles = self.env['fleet.vehicle'].sudo().search(
            [('gps_device_identifier', 'in', list(device_identifiers))])
        vehicle_by_identifier = {vehicle.gps_device_identifier: vehicle for vehicle in vehicles}
        create_values_list = []
        for position_values in position_values_list:
            vehicle = vehicle_by_identifier.get(position_values['device_identifier'])
            if not vehicle:
                continue
            create_values_list.append({
                **{key: position_value for key, position_value in position_values.items()
                   if key != 'device_identifier'},
                'source': 'device',
                'vehicle_id': vehicle.id,
                'employee_id': vehicle.driver_employee_id.id,
                'company_id': vehicle.company_id.id or self.env.company.id,
            })
        positions = self.sudo().create(create_values_list)
        for vehicle in positions.vehicle_id:
            vehicle_positions = positions.filtered(lambda position: position.vehicle_id == vehicle)
            vehicle._update_live_position(max(vehicle_positions, key=lambda position: position.recorded_at))
        return positions, sorted(device_identifiers - set(vehicle_by_identifier))

    @api.model
    def _cron_purge_old_positions(self):
        retention_days = int(self.env['ir.config_parameter'].sudo().get_param(
            'arbz_delivery_route.position_retention_days', 90))
        cutoff_datetime = fields.Datetime.now() - timedelta(days=retention_days)
        self.sudo().search([('recorded_at', '<', cutoff_datetime)]).unlink()
