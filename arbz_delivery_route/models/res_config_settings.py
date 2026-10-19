# -*- coding: utf-8 -*-
from odoo import fields, models


class ResConfigSettings(models.TransientModel):
    _inherit = 'res.config.settings'

    logistics_geofence_radius_m = fields.Integer(
        string='Geofence Radius (m)', default=300,
        config_parameter='arbz_delivery_route.geofence_radius_m',
        help='Deliveries registered farther than this distance from the branch are flagged. 0 disables it.')
    logistics_require_proof_of_delivery = fields.Boolean(
        string='Require Proof of Delivery',
        config_parameter='arbz_delivery_route.require_proof_of_delivery',
        help='A signature or a photo is mandatory to mark a stop as delivered.')
    logistics_api_key_days = fields.Integer(
        string='Mobile Session Duration (days)', default=30,
        config_parameter='arbz_delivery_route.api_key_days',
        help='Validity of the API key generated when a driver logs into the mobile app.')
    logistics_routing_provider = fields.Selection([
        ('vroom', 'VROOM / OpenRouteService (open source)'),
        ('google', 'Google Route Optimization API'),
    ], string='Routing Engine', default='vroom', config_parameter='arbz_delivery_route.routing_provider')
    logistics_vroom_url = fields.Char(
        string='VROOM URL', config_parameter='arbz_delivery_route.vroom_url',
        help='Leave empty to use OpenRouteService (https://api.openrouteservice.org/optimization) or set the '
             'URL of your self-hosted VROOM server, e.g. http://vroom:3000/')
    logistics_vroom_api_key = fields.Char(
        string='OpenRouteService API Key', config_parameter='arbz_delivery_route.vroom_api_key')
    logistics_vroom_profile = fields.Char(
        string='Routing Profile', default='driving-car', config_parameter='arbz_delivery_route.vroom_profile',
        help='driving-car for OpenRouteService, car for a default self-hosted VROOM + OSRM.')
    logistics_google_project_id = fields.Char(
        string='Google Cloud Project', config_parameter='arbz_delivery_route.google_project_id')
    logistics_google_service_account = fields.Char(
        string='Google Service Account Key (JSON)', config_parameter='arbz_delivery_route.google_service_account')
    logistics_depot_partner_id = fields.Many2one(
        'res.partner', string='Depot', config_parameter='arbz_delivery_route.depot_partner_id',
        help='Where the vehicles leave from and return to. Defaults to the company address.')
    logistics_shift_start = fields.Float(
        string='Shift Start', default=7.0, config_parameter='arbz_delivery_route.shift_start')
    logistics_shift_end = fields.Float(
        string='Shift End', default=19.0, config_parameter='arbz_delivery_route.shift_end')
    logistics_service_minutes = fields.Integer(
        string='Minutes per Stop', default=15, config_parameter='arbz_delivery_route.service_minutes')
    logistics_position_retention_days = fields.Integer(
        string='GPS History Retention (days)', default=90,
        config_parameter='arbz_delivery_route.position_retention_days')
