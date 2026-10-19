# -*- coding: utf-8 -*-
{
    'name': 'Logistics',
    'summary': 'Plan delivery routes, track drivers on a map and operate them from a mobile app',
    'description': """
Logistics routes
================
* Daily stops per driver (hr.employee) and vehicle, ordered by sequence.
* Single state machine shared by the back-office and the mobile API.
* Map view with optimized routing (web_map / Mapbox) and live driver positions.
* Route optimization (VROOM / OpenRouteService or Google Route Optimization API).
* Proof of delivery: signature, photos, receiver name and geofence check.
* REST API v1 (bearer API keys) with offline sync for the drivers' mobile app.
""",
    'author': 'Jesus Ramirez',
    'website': 'https://airibuz.com',
    'category': 'Operations/Inventory',
    'version': '19.0.2.0.0',
    'license': 'LGPL-3',
    'depends': [
        'mail',
        'sale',
        'purchase',
        'hr_fleet',
        'base_geolocalize',
        'web_map',
        'l10n_mx_edi_extended',
    ],
    'data': [
        'security/security.xml',
        'security/ir.model.access.csv',
        'data/logistics_sequence.xml',
        'data/ir_cron.xml',
        'report/delivery_proof_report.xml',
        'views/res_partner_views.xml',
        'views/logistics_route_views.xml',
        'views/hr_employee_views.xml',
        'views/fleet_vehicle_views.xml',
        'views/res_config_settings_views.xml',
        'wizard/logistics_route_optimize_wizard_views.xml',
        'views/logistics_menus.xml',
    ],
    'installable': True,
    'application': True,
    'auto_install': False,
}
