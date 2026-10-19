# -*- coding: utf-8 -*-
import json
from datetime import date, datetime
from unittest.mock import patch

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from odoo.exceptions import UserError
from odoo.tests import tagged

from .common import LogisticsCommon

OPTIMIZER_REQUESTS_POST = 'odoo.addons.arbz_delivery_route.models.logistics_route_optimizer.requests.post'
ROUTE_DATE = date(2026, 10, 19)


class FakeProviderResponse:
    def __init__(self, response_body, status_code=200):
        self.status_code = status_code
        self._response_body = response_body
        self.text = json.dumps(response_body)

    def json(self):
        return self._response_body


@tagged('post_install', '-at_install')
class TestRouteOptimizer(LogisticsCommon):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env.company.partner_id.write({
            'partner_latitude': 19.40, 'partner_longitude': -99.10, 'tz': 'America/Mexico_City'})
        vehicle_model = cls.vehicle.model_id
        cls.other_vehicle = cls.env['fleet.vehicle'].create({
            'model_id': vehicle_model.id, 'license_plate': 'XYZ-987',
            'driver_employee_id': cls.other_driver_employee.id})
        cls.second_branch = cls.env['res.partner'].create({
            'name': 'Branch 2', 'partner_latitude': 19.50, 'partner_longitude': -99.20})
        (cls.route | cls.other_route).write({'date': ROUTE_DATE, 'driver_employee_id': False, 'vehicle_id': False})
        cls.other_route.write({'branch_id': cls.second_branch.id, 'lunch_start_time': 14.0, 'lunch_end_time': 15.0})
        (cls.route | cls.other_route).with_user(cls.supervisor_user).action_request()

    def _run_wizard(self, **wizard_values):
        wizard = self.env['logistics.route.optimize.wizard'].with_user(self.supervisor_user).create({
            'route_date': ROUTE_DATE,
            'driver_employee_ids': [(6, 0, (self.driver_employee | self.other_driver_employee).ids)],
            **wizard_values,
        })
        return wizard.action_optimize()

    def test_vroom_assigns_orders_and_confirms(self):
        vroom_solution = {'code': 0, 'unassigned': [], 'routes': [
            {'vehicle': self.other_driver_employee.id, 'steps': [
                {'type': 'start'},
                {'type': 'job', 'job': self.other_route.id, 'arrival': 9 * 3600, 'waiting_time': 0},
                {'type': 'job', 'job': self.route.id, 'arrival': 10 * 3600, 'waiting_time': 600},
                {'type': 'end'},
            ]},
        ]}
        with patch(OPTIMIZER_REQUESTS_POST, return_value=FakeProviderResponse(vroom_solution)) as mocked_post:
            self._run_wizard()
        request_payload = mocked_post.call_args.kwargs['json']
        self.assertEqual(len(request_payload['vehicles']), 2)
        self.assertEqual(request_payload['vehicles'][0]['start'], [-99.10, 19.40], 'VROOM expects [lon, lat]')
        lunch_job = next(job for job in request_payload['jobs'] if job['id'] == self.other_route.id)
        self.assertEqual(lunch_job['time_windows'], [[9 * 3600, 14 * 3600], [15 * 3600, 18 * 3600]])
        self.assertNotIn('skills', lunch_job)

        self.assertEqual(self.route.driver_employee_id, self.other_driver_employee)
        self.assertEqual(self.route.vehicle_id, self.other_vehicle)
        self.assertEqual((self.other_route.sequence, self.route.sequence), (10, 20))
        # 10:10 in Mexico City (UTC-6) = 16:10 UTC
        self.assertEqual(self.route.planned_arrival, datetime(2026, 10, 19, 16, 10))
        self.assertEqual(self.route.state, 'assigned')

    def test_keep_assignment_uses_skills(self):
        self.route.write({'driver_employee_id': self.driver_employee.id})
        vroom_solution = {'code': 0, 'routes': [], 'unassigned': [{'id': self.route.id}, {'id': self.other_route.id}]}
        with patch(OPTIMIZER_REQUESTS_POST, return_value=FakeProviderResponse(vroom_solution)) as mocked_post:
            notification = self._run_wizard(keep_assignment=True)
        pinned_job = next(job for job in mocked_post.call_args.kwargs['json']['jobs'] if job['id'] == self.route.id)
        self.assertEqual(pinned_job['skills'], [self.driver_employee.id])
        self.assertEqual(notification['params']['type'], 'warning')
        self.assertEqual(self.route.state, 'requested')

    def test_provider_error_is_user_error(self):
        with patch(OPTIMIZER_REQUESTS_POST, return_value=FakeProviderResponse({'error': 'quota'}, 403)):
            with self.assertRaises(UserError):
                self._run_wizard()

    def test_google_provider(self):
        private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        private_key_pem = private_key.private_bytes(
            serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()).decode()
        config_parameters = self.env['ir.config_parameter'].sudo()
        config_parameters.set_param('arbz_delivery_route.routing_provider', 'google')
        config_parameters.set_param('arbz_delivery_route.google_service_account', json.dumps({
            'client_email': 'optimizer@test-project.iam.gserviceaccount.com',
            'private_key': private_key_pem,
            'project_id': 'test-project',
        }))
        google_solution = {'routes': [{
            'vehicleLabel': str(self.driver_employee.id),
            'visits': [{'shipmentLabel': str(self.route.id), 'startTime': '2026-10-19T15:30:00Z'}],
        }], 'skippedShipments': [{'index': 1, 'label': str(self.other_route.id)}]}
        provider_responses = [FakeProviderResponse({'access_token': 'token-123'}), FakeProviderResponse(google_solution)]
        with patch(OPTIMIZER_REQUESTS_POST, side_effect=provider_responses) as mocked_post:
            self._run_wizard()
        token_call, optimize_call = mocked_post.call_args_list
        signed_jwt = token_call.kwargs['data']['assertion']
        self.assertEqual(signed_jwt.count('.'), 2)
        self.assertIn('projects/test-project:optimizeTours', optimize_call.args[0])
        self.assertEqual(optimize_call.kwargs['headers']['Authorization'], 'Bearer token-123')
        shipments = optimize_call.kwargs['json']['model']['shipments']
        self.assertEqual(shipments[0]['deliveries'][0]['timeWindows'][0]['startTime'], '2026-10-19T15:00:00Z')

        self.assertEqual(self.route.driver_employee_id, self.driver_employee)
        self.assertEqual(self.route.planned_arrival, datetime(2026, 10, 19, 15, 30))
        self.assertFalse(self.other_route.driver_employee_id)
        self.assertEqual(self.other_route.state, 'requested')
