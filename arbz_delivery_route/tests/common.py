# -*- coding: utf-8 -*-
from odoo.tests.common import TransactionCase, new_test_user


class LogisticsCommon(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.supervisor_user = new_test_user(
            cls.env, login='logistics_supervisor', groups='arbz_delivery_route.group_logistics_supervisor')
        cls.driver_user = new_test_user(
            cls.env, login='logistics_driver', password='driver-password-123',
            groups='arbz_delivery_route.group_logistics_driver')
        cls.other_driver_user = new_test_user(
            cls.env, login='logistics_driver_2', groups='arbz_delivery_route.group_logistics_driver')
        cls.driver_employee = cls.env['hr.employee'].create({
            'name': 'Driver One', 'user_id': cls.driver_user.id, 'is_logistics_driver': True})
        cls.other_driver_employee = cls.env['hr.employee'].create({
            'name': 'Driver Two', 'user_id': cls.other_driver_user.id, 'is_logistics_driver': True})
        vehicle_brand = cls.env['fleet.vehicle.model.brand'].create({'name': 'Test Brand'})
        vehicle_model = cls.env['fleet.vehicle.model'].create({'name': 'Van', 'brand_id': vehicle_brand.id})
        cls.vehicle = cls.env['fleet.vehicle'].create({
            'model_id': vehicle_model.id,
            'license_plate': 'ABC-123',
            'driver_employee_id': cls.driver_employee.id,
            'gps_device_identifier': '359710000000001',
        })
        cls.customer = cls.env['res.partner'].create({'name': 'Customer'})
        cls.branch = cls.env['res.partner'].create({
            'name': 'Branch', 'parent_id': cls.customer.id,
            'partner_latitude': 19.4326, 'partner_longitude': -99.1332})
        cls.route = cls._create_route(cls.driver_employee)
        cls.other_route = cls._create_route(cls.other_driver_employee)

    @classmethod
    def _create_route(cls, driver_employee, **extra_values):
        return cls.env['logistics.route'].create({
            'customer_id': cls.customer.id,
            'branch_id': cls.branch.id,
            'attendant_id': cls.customer.id,
            'payment_method': 'cash',
            'payment_type': 'single',
            'service_type': 'direct',
            'start_time': 9.0,
            'end_time': 18.0,
            'driver_employee_id': driver_employee.id,
            'vehicle_id': cls.vehicle.id,
            **extra_values,
        })

    def _assign(self, route):
        route.with_user(self.supervisor_user).action_request()
        route.with_user(self.supervisor_user).action_assign()
