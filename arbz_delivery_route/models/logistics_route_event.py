# -*- coding: utf-8 -*-
from odoo import fields, models


class LogisticsRouteEvent(models.Model):
    """Immutable log of what the driver did on each stop (audit + offline sync)."""
    _name = 'logistics.route.event'
    _description = 'Logistics Route Event'
    _order = 'event_datetime desc, id desc'

    _sync_uuid_unique = models.Constraint(
        'UNIQUE(sync_uuid)', 'This event was already synchronized.')

    route_id = fields.Many2one('logistics.route', required=True, index=True, ondelete='cascade')
    company_id = fields.Many2one(related='route_id.company_id', store=True, index=True)
    employee_id = fields.Many2one('hr.employee', string='Driver', index=True)
    event_type = fields.Selection([
        ('start', 'Started'),
        ('arrive', 'Arrived'),
        ('deliver', 'Delivered'),
        ('fail', 'Failed'),
        ('note', 'Note'),
    ], required=True)
    event_datetime = fields.Datetime(
        required=True, default=fields.Datetime.now, help='Time recorded on the device.')
    received_at = fields.Datetime(
        required=True, default=fields.Datetime.now, help='Time the server received the event.')
    latitude = fields.Float(digits=(10, 7))
    longitude = fields.Float(digits=(10, 7))
    note = fields.Text()
    sync_uuid = fields.Char(string='Sync UUID', index=True, copy=False)
