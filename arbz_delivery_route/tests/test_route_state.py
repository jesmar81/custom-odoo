# -*- coding: utf-8 -*-
from odoo.exceptions import AccessError, UserError
from odoo.tests import tagged

from .common import LogisticsCommon


@tagged('post_install', '-at_install')
class TestRouteState(LogisticsCommon):

    def test_happy_path(self):
        self._assign(self.route)
        driver_route = self.route.with_user(self.driver_user)
        driver_route.action_start()
        driver_route.action_mark_arrived()
        driver_route.action_deliver()
        self.assertEqual(self.route.state, 'delivered')
        self.assertTrue(self.route.started_at and self.route.arrived_at and self.route.delivered_at)

    def test_invalid_transition(self):
        with self.assertRaises(UserError):
            self.route.with_user(self.supervisor_user).action_assign()
        self._assign(self.route)
        self.route.with_user(self.driver_user).action_start()
        with self.assertRaises(UserError):
            self.route.with_user(self.supervisor_user).action_cancel()

    def test_postpone_requires_reason(self):
        self._assign(self.route)
        self.route.with_user(self.driver_user).action_start()
        with self.assertRaises(UserError):
            self.route.with_user(self.driver_user).action_postpone()

    def test_driver_cannot_operate_foreign_route(self):
        self._assign(self.other_route)
        with self.assertRaises(AccessError):
            self.other_route.with_user(self.driver_user).action_start()

    def test_driver_only_sees_own_routes(self):
        visible_routes = self.env['logistics.route'].with_user(self.driver_user).search([])
        self.assertEqual(visible_routes, self.route)

    def test_driver_cannot_supervise(self):
        self.route.with_user(self.supervisor_user).action_request()
        with self.assertRaises(AccessError):
            self.route.with_user(self.driver_user).action_assign()

    def test_proof_required(self):
        self.env['ir.config_parameter'].sudo().set_param('arbz_delivery_route.require_proof_of_delivery', True)
        self._assign(self.route)
        self.route.with_user(self.driver_user).action_start()
        with self.assertRaises(UserError):
            self.route.with_user(self.driver_user).action_deliver()

    def test_geofence(self):
        self.env['ir.config_parameter'].sudo().set_param('arbz_delivery_route.geofence_radius_m', 300)
        self.route.write({'delivery_latitude': 19.4426, 'delivery_longitude': -99.1332})
        self.assertAlmostEqual(self.route.geofence_distance_m, 1112, delta=5)
        self.assertTrue(self.route.geofence_exceeded)

    def test_device_positions_update_vehicle_marker(self):
        positions, unknown_device_identifiers = self.env['logistics.position']._record_device_positions([
            {'device_identifier': '359710000000001', 'latitude': 19.5, 'longitude': -99.2, 'speed_kmh': 40.0},
            {'device_identifier': 'unknown-imei', 'latitude': 19.5, 'longitude': -99.2},
        ])
        self.assertEqual(len(positions), 1)
        self.assertEqual(positions.employee_id, self.driver_employee)
        self.assertEqual(unknown_device_identifiers, ['unknown-imei'])
        self.assertAlmostEqual(self.vehicle.logistics_tracking_partner_id.partner_latitude, 19.5)
