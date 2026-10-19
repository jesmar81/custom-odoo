# -*- coding: utf-8 -*-
from odoo import _, fields, models


class FleetVehicle(models.Model):
    _inherit = 'fleet.vehicle'

    _gps_device_identifier_unique = models.Constraint(
        'UNIQUE(gps_device_identifier)', 'This GPS tracker is already installed in another vehicle.')

    gps_device_identifier = fields.Char(
        string='GPS Tracker ID', copy=False, index=True, tracking=True,
        help='IMEI or unique id that the GPS platform sends with every position.')
    logistics_tracking_partner_id = fields.Many2one(
        'res.partner', string='Live Position Marker', copy=False, readonly=True)
    logistics_last_position_at = fields.Datetime(string='Last Position At', copy=False, readonly=True)
    logistics_last_speed_kmh = fields.Float(string='Last Speed (km/h)', copy=False, readonly=True)
    logistics_position_ids = fields.One2many('logistics.position', 'vehicle_id', string='GPS Positions')

    def _update_live_position(self, position):
        self.ensure_one()
        vehicle = self.sudo()
        if not vehicle.logistics_tracking_partner_id:
            vehicle.logistics_tracking_partner_id = self.env['res.partner'].sudo().create({
                'name': _('%s (GPS)', vehicle.display_name),
                'type': 'other',
                'parent_id': (vehicle.company_id or self.env.company).partner_id.id,
                'company_id': vehicle.company_id.id,
            })
        if position.recorded_at < (vehicle.logistics_last_position_at or position.recorded_at):
            return
        vehicle.logistics_tracking_partner_id.write({
            'partner_latitude': position.latitude,
            'partner_longitude': position.longitude,
            'date_localization': fields.Date.to_date(position.recorded_at),
        })
        vehicle.write({
            'logistics_last_position_at': position.recorded_at,
            'logistics_last_speed_kmh': position.speed_kmh,
        })
