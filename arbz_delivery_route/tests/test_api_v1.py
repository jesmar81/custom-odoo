# -*- coding: utf-8 -*-
import base64
import json

from odoo.tests import HttpCase, tagged, new_test_user

from .common import LogisticsCommon

API_PREFIX = '/api/logistics/v1'
TINY_PNG_BASE64 = (
    'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg==')


@tagged('post_install', '-at_install')
class TestApiV1(HttpCase, LogisticsCommon):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.driver_token = cls.env['res.users.apikeys'].with_user(cls.driver_user).sudo()._generate(
            None, 'Logistics mobile app: test', None)
        cls.gps_user = new_test_user(
            cls.env, login='gps_integration', groups='base.group_user,arbz_delivery_route.group_logistics_gps_integration')
        cls.gps_token = cls.env['res.users.apikeys'].with_user(cls.gps_user).sudo()._generate(
            None, 'Traccar', None)

    def _call(self, method, path, payload=None, token=None):
        headers = {'Content-Type': 'application/json'}
        if token:
            headers['Authorization'] = f'Bearer {token}'
        response = self.url_open(
            f'{API_PREFIX}{path}', data=json.dumps(payload) if payload is not None else None,
            headers=headers, method=method)
        return response.status_code, response.json()

    def test_requires_token(self):
        status_code, _body = self._call('GET', '/me')
        self.assertEqual(status_code, 401)

    def test_login(self):
        status_code, body = self._call('POST', '/auth/login', {
            'login': 'logistics_driver', 'password': 'driver-password-123', 'device_name': 'phone'})
        self.assertEqual(status_code, 200, body)
        status_code, body = self._call('GET', '/me', token=body['data']['token'])
        self.assertEqual(status_code, 200)
        self.assertEqual(body['data']['employee_id'], self.driver_employee.id)

    def test_login_wrong_password(self):
        status_code, body = self._call('POST', '/auth/login', {'login': 'logistics_driver', 'password': 'nope'})
        self.assertEqual(status_code, 401)
        self.assertNotIn('debug', json.dumps(body))

    def test_list_routes_only_own(self):
        self._assign(self.route)
        self._assign(self.other_route)
        status_code, body = self._call('GET', '/routes', token=self.driver_token)
        self.assertEqual(status_code, 200, body)
        self.assertEqual([route['id'] for route in body['data']['routes']], [self.route.id])

    def test_foreign_route_not_found(self):
        self._assign(self.other_route)
        status_code, _body = self._call('GET', f'/routes/{self.other_route.id}', token=self.driver_token)
        self.assertEqual(status_code, 404)

    def test_deliver_with_proof(self):
        self._assign(self.route)
        self._call('POST', f'/routes/{self.route.id}/actions/start', {}, token=self.driver_token)
        status_code, body = self._call('POST', f'/routes/{self.route.id}/actions/deliver', {
            'received_by_name': 'Juan',
            'signature': TINY_PNG_BASE64,
            'photos': [TINY_PNG_BASE64],
            'latitude': 19.4327,
            'longitude': -99.1333,
        }, token=self.driver_token)
        self.assertEqual(status_code, 200, body)
        self.assertEqual(self.route.state, 'delivered')
        self.assertEqual(self.route.received_by_name, 'Juan')
        self.assertEqual(len(self.route.delivery_photo_ids), 1)
        self.assertEqual(base64.b64decode(self.route.delivery_signature), base64.b64decode(TINY_PNG_BASE64))

    def test_invalid_transition_conflict(self):
        status_code, body = self._call('POST', f'/routes/{self.route.id}/actions/deliver', {},
                                       token=self.driver_token)
        self.assertEqual(status_code, 404, body)  # draft stops are not exposed to the driver
        self._assign(self.route)
        status_code, body = self._call('POST', f'/routes/{self.route.id}/actions/deliver', {},
                                       token=self.driver_token)
        self.assertEqual(status_code, 409, body)

    def test_offline_sync_is_idempotent(self):
        self._assign(self.route)
        events = [
            {'sync_uuid': 'uuid-1', 'route_id': self.route.id, 'action': 'start',
             'event_datetime': '2026-01-01T08:00:00Z'},
            {'sync_uuid': 'uuid-2', 'route_id': self.route.id, 'action': 'arrive'},
            {'sync_uuid': 'uuid-3', 'route_id': self.other_route.id, 'action': 'start'},
        ]
        status_code, body = self._call('POST', '/sync', {'events': events}, token=self.driver_token)
        self.assertEqual(status_code, 200, body)
        self.assertEqual([result['status'] for result in body['data']['results']],
                         ['applied', 'applied', 'rejected'])
        status_code, body = self._call('POST', '/sync', {'events': events[:1]}, token=self.driver_token)
        self.assertEqual(body['data']['results'][0]['status'], 'duplicate')
        self.assertEqual(self.env['logistics.route.event'].search_count([('route_id', '=', self.route.id)]), 2)
        self.assertEqual(self.route.state, 'on_route')

    def test_app_positions(self):
        status_code, body = self._call('POST', '/positions', {'positions': [
            {'latitude': 19.40, 'longitude': -99.10, 'recorded_at': '2026-01-01T08:00:00Z'},
            {'latitude': 19.41, 'longitude': -99.11, 'recorded_at': '2026-01-01T08:01:00Z'},
        ]}, token=self.driver_token)
        self.assertEqual(status_code, 201, body)
        self.assertAlmostEqual(self.driver_employee.sudo().logistics_tracking_partner_id.partner_latitude, 19.41)

    def test_invalid_position_rejected(self):
        status_code, _body = self._call('POST', '/positions', {'positions': [{'latitude': 200, 'longitude': 0}]},
                                        token=self.driver_token)
        self.assertEqual(status_code, 422)

    def test_traccar_forwarding(self):
        status_code, body = self._call('POST', '/devices/traccar', {
            'device': {'uniqueId': '359710000000001'},
            'position': {'latitude': 19.45, 'longitude': -99.15, 'speed': 10, 'course': 90,
                         'fixTime': '2026-01-01T08:00:00.000+00:00', 'attributes': {'ignition': True}},
        }, token=self.gps_token)
        self.assertEqual(status_code, 201, body)
        self.assertAlmostEqual(self.vehicle.logistics_last_speed_kmh, 18.52)

    def test_driver_cannot_push_device_positions(self):
        status_code, _body = self._call('POST', '/devices/positions', {'positions': [
            {'device_id': '359710000000001', 'latitude': 19.4, 'longitude': -99.1}]}, token=self.driver_token)
        self.assertEqual(status_code, 403)
