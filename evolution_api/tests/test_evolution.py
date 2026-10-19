import json
from unittest.mock import MagicMock, patch

import requests

from odoo.tests import HttpCase, TransactionCase, tagged

from odoo.addons.evolution_api.models.evolution_message import normalize_phone

SEND_PATH = 'odoo.addons.evolution_api.models.evolution_message.requests.post'


def _fake_response(payload=None, status_ok=True):
    response = MagicMock()
    response.json.return_value = payload or {'key': {'id': 'ABC123'}}
    if not status_ok:
        response.raise_for_status.side_effect = requests.exceptions.HTTPError("500 server error")
    return response


@tagged('post_install', '-at_install')
class TestEvolutionMessage(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        parameters = cls.env['ir.config_parameter'].sudo()
        parameters.set_param('evolution_api.enabled', 'True')
        parameters.set_param('evolution_api.base_url', 'https://evo.example.com/')
        parameters.set_param('evolution_api.instance', 'compras')
        parameters.set_param('evolution_api.api_key', 'secret-key')
        cls.vendor = cls.env['res.partner'].create({'name': 'Proveedor Uno', 'phone': '+52 (55) 1234-5678'})

    def test_phone_normalization(self):
        self.assertEqual(normalize_phone('55 1234 5678', '52'), '525512345678')
        self.assertEqual(normalize_phone('+52 55 1234 5678', '52'), '525512345678')
        self.assertEqual(normalize_phone('0052 55 1234 5678', '52'), '525512345678')
        self.assertEqual(normalize_phone('', '52'), '')

    def test_send_text_calls_evolution_with_apikey(self):
        with patch(SEND_PATH, return_value=_fake_response()) as post:
            message = self.env['evolution.message']._send_text(self.vendor.phone, "Hola", partner=self.vendor)
        url, = post.call_args.args
        self.assertEqual(url, 'https://evo.example.com/message/sendText/compras')
        self.assertEqual(post.call_args.kwargs['headers']['apikey'], 'secret-key')
        self.assertEqual(post.call_args.kwargs['json'], {'number': '525512345678', 'text': 'Hola'})
        self.assertEqual((message.state, message.external_id), ('sent', 'ABC123'))

    def test_failed_send_is_logged_without_raising(self):
        with patch(SEND_PATH, return_value=_fake_response(status_ok=False)):
            message = self.env['evolution.message']._send_text(self.vendor.phone, "Hola")
        self.assertEqual(message.state, 'failed')
        self.assertNotIn('secret-key', message.error)

    def test_invalid_number_is_not_sent(self):
        with patch(SEND_PATH) as post:
            message = self.env['evolution.message']._send_text('123', "Hola")
        post.assert_not_called()
        self.assertEqual(message.state, 'failed')

    def test_disabled_connector_raises(self):
        self.env['ir.config_parameter'].sudo().set_param('evolution_api.enabled', '')
        with self.assertRaises(Exception):
            self.env['evolution.message']._send_text(self.vendor.phone, "Hola")

    def test_reply_is_posted_in_the_document_that_started_the_conversation(self):
        order = self.env['res.partner'].create({'name': 'Documento de prueba'})
        with patch(SEND_PATH, return_value=_fake_response()):
            self.env['evolution.message']._send_text(self.vendor.phone, "Cotizacion?", partner=self.vendor, document=order)
        incoming = self.env['evolution.message']._register_incoming('525512345678', "$100 por pieza", external_id='IN1')
        self.assertEqual((incoming.res_model, incoming.res_id, incoming.partner_id), ('res.partner', order.id, self.vendor))
        self.assertIn('$100', order.message_ids[0].body)
        # webhook redelivery does not duplicate
        self.assertFalse(self.env['evolution.message']._register_incoming('525512345678', "$100", external_id='IN1'))


@tagged('post_install', '-at_install')
class TestEvolutionWebhook(HttpCase):

    def _post(self, payload, token):
        return self.url_open(
            f'/evolution_api/webhook?token={token}',
            data=json.dumps(payload), headers={'Content-Type': 'application/json'},
        )

    def test_webhook_security_and_processing(self):
        self.env['res.config.settings']._evolution_ensure_webhook_token()
        token = self.env['ir.config_parameter'].sudo().get_param('evolution_api.webhook_token')
        vendor = self.env['res.partner'].create({'name': 'Proveedor Web', 'phone': '55 9999 0000'})
        event = {
            'event': 'messages.upsert', 'instance': 'compras',
            'data': {
                'key': {'remoteJid': '525599990000@s.whatsapp.net', 'fromMe': False, 'id': 'W1'},
                'message': {'conversation': 'Tengo 50 piezas'}, 'messageType': 'conversation',
            },
        }
        self.assertEqual(self._post(event, 'wrong-token').status_code, 403)
        self.assertEqual(self.url_open('/evolution_api/webhook').status_code, 405)  # GET not allowed
        self.assertEqual(self._post(event, token).status_code, 200)
        message = self.env['evolution.message'].search([('external_id', '=', 'W1')])
        self.assertEqual((message.body, message.partner_id), ('Tengo 50 piezas', vendor))
        # own messages, groups and other events are ignored
        event['data']['key'].update(fromMe=True, id='W2')
        self._post(event, token)
        event['data']['key'].update(fromMe=False, remoteJid='1234@g.us', id='W3')
        self._post(event, token)
        self._post({'event': 'MESSAGES_UPDATE', 'data': {}}, token)
        self.assertFalse(self.env['evolution.message'].search([('external_id', 'in', ('W2', 'W3'))]))
        # uppercase event name variant
        event.update(event='MESSAGES_UPSERT')
        event['data']['key'].update(remoteJid='525599990000@s.whatsapp.net', id='W4')
        self._post(event, token)
        self.assertTrue(self.env['evolution.message'].search([('external_id', '=', 'W4')]))
