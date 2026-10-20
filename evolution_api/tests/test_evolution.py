import base64
import json
from datetime import timedelta
from unittest.mock import MagicMock, patch

import requests

from odoo import fields
from odoo.exceptions import AccessError, UserError
from odoo.tests import HttpCase, TransactionCase, tagged
from odoo.tools import file_open

from odoo.addons.evolution_api.utils.phone import normalize_phone, phone_tail

HTTP_PATH = 'odoo.addons.evolution_api.models.evolution_account.requests.request'


def _fake_response(payload=None, ok=True):
    response = MagicMock()
    response.json.return_value = payload or {'key': {'id': 'WA-1'}}
    if not ok:
        response.raise_for_status.side_effect = requests.exceptions.HTTPError("500 server error")
    return response


class EvolutionCase(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        group_user = cls.env.ref('base.group_user')
        cls.alice = cls.env['res.users'].create({
            'name': 'Alice Member', 'login': 'alice_evo', 'email': 'alice@example.com',
            'group_ids': [(6, 0, [group_user.id, cls.env.ref('evolution_api.group_evolution_user').id])],
        })
        cls.bob = cls.env['res.users'].create({
            'name': 'Bob Outsider', 'login': 'bob_evo', 'email': 'bob@example.com',
            'group_ids': [(6, 0, [group_user.id, cls.env.ref('evolution_api.group_evolution_user').id])],
        })
        cls.account = cls.env['evolution.account'].create({
            'name': 'Purchases', 'base_url': 'https://evo.example.com/', 'instance': 'compras', 'api_key': 'secret-key',
            'user_ids': [(6, 0, cls.alice.ids)], 'daily_limit': 5, 'max_per_contact_per_day': 3,
            'min_delay_seconds': 0, 'max_delay_seconds': 0, 'send_from_hour': 0.0, 'send_until_hour': 24.0, 'tz': 'UTC',
        })
        cls.other_account = cls.env['evolution.account'].create({
            'name': 'Sales', 'base_url': 'https://evo.example.com', 'instance': 'ventas', 'api_key': 'other-key',
            'user_ids': [(6, 0, cls.bob.ids)], 'send_from_hour': 0.0, 'send_until_hour': 24.0, 'tz': 'UTC',
        })
        cls.vendor = cls.env['res.partner'].create({'name': 'Proveedor Uno', 'phone': '+52 (55) 1234-5678'})
        cls.Message = cls.env['evolution.message']

    def _queue(self, body="Hola", partner=None, **kwargs):
        return self.Message.with_user(self.alice)._queue_message(
            self.account, '55 1234 5678', body, partner=partner or self.vendor, author=self.alice, **kwargs,
        )

    def _send_cycle(self):
        self.Message._cron_send_queue()


@tagged('post_install', '-at_install')
class TestPhone(TransactionCase):

    def test_phone_normalization(self):
        self.assertEqual(normalize_phone('55 1234 5678', '52'), '525512345678')
        self.assertEqual(normalize_phone('+52 55 1234 5678', '52'), '525512345678')
        self.assertEqual(normalize_phone('0052 55 1234 5678', '52'), '525512345678')
        self.assertEqual(normalize_phone('', '52'), '')
        self.assertEqual(phone_tail('525512345678'), '5512345678')


@tagged('post_install', '-at_install')
class TestAccessAndPolicy(EvolutionCase):

    def test_member_can_queue_outsider_cannot(self):
        messages = self._queue()
        self.assertEqual((messages.state, messages.phone, messages.account_id), ('queued', '525512345678', self.account))
        self.assertEqual(messages.conversation_id.partner_id, self.vendor)
        with self.assertRaises(AccessError):
            self.Message.with_user(self.bob)._queue_message(self.account, '55 1234 5678', "x", author=self.bob)

    def test_visibility_follows_the_account(self):
        conversation = self._queue().conversation_id
        self.assertIn(conversation, self.env['evolution.conversation'].with_user(self.alice).search([]))
        self.assertNotIn(conversation, self.env['evolution.conversation'].with_user(self.bob).search([]))
        self.assertNotIn(self.account, self.env['evolution.account'].with_user(self.bob).search([]))
        self.assertTrue(self.env['evolution.account'].with_user(self.alice).search([('id', '=', self.account.id)]))
        with self.assertRaises(AccessError):
            self.account.with_user(self.alice).api_key  # noqa: B018 - only administrators read the key

    def test_unknown_numbers_are_blocked_until_they_write_first(self):
        with self.assertRaisesRegex(UserError, "not a known contact"):
            self.Message._queue_message(self.account, '55 9999 0000', "Hola", author=self.alice)
        self.Message._register_incoming(self.account, '525599990000', "Buenas")
        self.assertTrue(self.Message._queue_message(self.account, '55 9999 0000', "Hola", author=self.alice))

    def test_daily_limit_per_contact_limit_and_duplicates(self):
        self._queue("uno")
        with self.assertRaisesRegex(UserError, "same message"):
            self._queue("uno")
        self._queue("dos")
        self._queue("tres")
        with self.assertRaisesRegex(UserError, "Too many messages to this contact"):
            self._queue("cuatro")
        other = self.env['res.partner'].create({'name': 'Otro', 'phone': '55 2222 3333'})
        for index in range(2):
            self.Message._queue_message(self.account, other.phone, f"msg {index}", partner=other, author=self.alice)
        with self.assertRaisesRegex(UserError, "daily limit"):
            self.Message._queue_message(self.account, other.phone, "extra", partner=other, author=self.alice)

    def test_opt_out_blocks_and_cancels_pending(self):
        pending = self._queue("pendiente")
        self.Message._register_incoming(self.account, '525512345678', "BAJA", external_id='STOP1')
        self.assertEqual(pending.state, 'cancelled')
        self.assertTrue(self.env['evolution.optout']._is_opted_out('525512345678'))
        with self.assertRaisesRegex(UserError, "asked not to receive"):
            self._queue("otro")

    def test_contact_policy_hook_blocks_everything(self):
        target = type(self.vendor)
        with patch.object(target, '_evolution_can_contact', lambda partner: "Handled manually" if partner == self.vendor else False):
            with self.assertRaisesRegex(UserError, "Handled manually"):
                self._queue()
            # even when only the phone number is given, the partner is found by it
            with self.assertRaisesRegex(UserError, "Handled manually"):
                self.Message._queue_message(self.account, '5512345678', "Hola", author=self.alice)

    def test_paused_account_does_not_queue(self):
        self.account.write({'is_paused': True, 'pause_reason': "test"})
        with self.assertRaisesRegex(UserError, "paused"):
            self._queue()


@tagged('post_install', '-at_install')
class TestSending(EvolutionCase):

    def test_text_goes_out_with_apikey_and_typing_delay(self):
        message = self._queue("Hola proveedor")
        with patch(HTTP_PATH, return_value=_fake_response()) as request_mock:
            self._send_cycle()
        args, kwargs = request_mock.call_args
        self.assertEqual(args, ('POST', 'https://evo.example.com/message/sendText/compras'))
        self.assertEqual(kwargs['headers']['apikey'], 'secret-key')
        self.assertEqual({'number': '525512345678', 'text': 'Hola proveedor'}, {k: kwargs['json'][k] for k in ('number', 'text')})
        self.assertTrue(1200 <= kwargs['json']['delay'] <= 3500)
        self.assertEqual((message.state, message.external_id), ('sent', 'WA-1'))

    def test_file_is_sent_as_media_with_caption(self):
        attachment = self.env['ir.attachment'].create({'name': 'cotizacion.pdf', 'raw': b'%PDF-1.4 test', 'mimetype': 'application/pdf'})
        message, = self._queue("Adjunto la cotización", attachments=attachment)
        self.assertEqual(attachment.res_model, 'evolution.message')
        with patch(HTTP_PATH, return_value=_fake_response()) as request_mock:
            self._send_cycle()
        args, kwargs = request_mock.call_args
        self.assertTrue(args[1].endswith('/message/sendMedia/compras'))
        body = kwargs['json']
        self.assertEqual((body['mediatype'], body['mimetype'], body['fileName'], body['caption']),
                         ('document', 'application/pdf', 'cotizacion.pdf', 'Adjunto la cotización'))
        self.assertEqual(base64.b64decode(body['media']), b'%PDF-1.4 test')
        self.assertEqual(message.state, 'sent')

    def test_several_files_make_several_messages(self):
        files = self.env['ir.attachment'].create([
            {'name': 'a.pdf', 'raw': b'a', 'mimetype': 'application/pdf'},
            {'name': 'b.png', 'raw': b'b', 'mimetype': 'image/png'},
        ])
        messages = self._queue("Texto", attachments=files)
        self.assertEqual(len(messages), 3)  # text + one per file
        self.assertEqual(self.account.sent_today, 3)

    def test_one_message_per_run_and_spacing(self):
        self.account.write({'min_delay_seconds': 3600, 'max_delay_seconds': 3600})
        first = self._queue("uno")
        second = self._queue("dos")
        with patch(HTTP_PATH, return_value=_fake_response()) as request_mock:
            self._send_cycle()
            self._send_cycle()
        self.assertEqual(request_mock.call_count, 1)
        self.assertEqual((first.state, second.state), ('sent', 'queued'))
        self.assertGreater(self.account.next_send_at, fields.Datetime.now() + timedelta(minutes=50))

    def test_outside_sending_hours_nothing_goes_out(self):
        hour = self.account._get_local_now().hour
        self.account.write({'send_from_hour': 13.0, 'send_until_hour': 14.0} if hour < 12 else {'send_from_hour': 0.0, 'send_until_hour': 1.0})
        message = self._queue()
        with patch(HTTP_PATH) as request_mock:
            self._send_cycle()
        request_mock.assert_not_called()
        self.assertEqual(message.state, 'queued')

    def test_failures_retry_then_give_up_then_pause(self):
        self.account.daily_limit = 50
        messages = [self._queue(f"intento {index}") for index in range(3)]
        with patch(HTTP_PATH, return_value=_fake_response(ok=False)):
            for __ in range(3):
                self._send_cycle()
        first = messages[0]
        self.assertEqual((first.state, first.attempts), ('queued', 1))  # waits before retrying
        self.assertTrue(first.retry_after)
        self.assertTrue(self.account.is_paused)  # 3 consecutive failures
        self.account.action_resume()
        self.assertFalse(self.account.is_paused)

    def test_too_large_file_fails_without_calling_the_server(self):
        self.env['ir.config_parameter'].sudo().set_param('evolution_api.max_file_mb', '1')
        attachment = self.env['ir.attachment'].create({'name': 'big.bin', 'raw': b'0' * (1024 * 1024 + 1)})
        message, = self._queue("Archivo", attachments=attachment)
        with patch(HTTP_PATH) as request_mock:
            self._send_cycle()
        request_mock.assert_not_called()
        self.assertEqual(message.state, 'failed')

    def test_document_gets_a_chatter_note(self):
        note_target = self.vendor
        message, = self._queue("Hola", document=note_target)
        with patch(HTTP_PATH, return_value=_fake_response()):
            self._send_cycle()
        self.assertIn("WhatsApp sent", note_target.message_ids[0].body)


@tagged('post_install', '-at_install')
class TestDrafts(EvolutionCase):

    def test_draft_waits_for_approval_and_is_not_counted(self):
        draft, = self._queue("Propuesta", draft=True, proposed_by="Agente Comprador")
        self.assertEqual(draft.state, 'draft')
        self.assertEqual(self.account.sent_today, 0)
        with patch(HTTP_PATH) as request_mock:
            self._send_cycle()
        request_mock.assert_not_called()
        with self.assertRaises(AccessError):
            draft.with_user(self.bob).action_approve()
        draft.with_user(self.alice).action_approve()
        self.assertEqual((draft.state, draft.approved_by_id), ('queued', self.alice))
        with patch(HTTP_PATH, return_value=_fake_response()):
            self._send_cycle()
        self.assertEqual(draft.state, 'sent')

    def test_reject_and_accounts_that_refuse_ai(self):
        draft, = self._queue("Propuesta", draft=True, proposed_by="Agente")
        draft.with_user(self.alice).action_reject()
        self.assertEqual(draft.state, 'cancelled')
        self.account.allow_ai_drafts = False
        with self.assertRaisesRegex(UserError, "does not accept drafts"):
            self._queue("Otra", draft=True, proposed_by="Agente")

    def test_approval_rechecks_the_policy(self):
        draft, = self._queue("Propuesta", draft=True, proposed_by="Agente")
        self.env['evolution.optout']._register_optout('525512345678', self.vendor, source='manual')
        draft.invalidate_recordset()
        # the opt-out already cancelled the draft
        self.assertEqual(draft.state, 'cancelled')


@tagged('post_install', '-at_install')
class TestReceiving(EvolutionCase):

    def test_reply_is_posted_in_the_document_and_deduplicated(self):
        self._queue("Cotizacion?", document=self.vendor)
        with patch(HTTP_PATH, return_value=_fake_response()):
            self._send_cycle()
        incoming = self.Message._register_incoming(self.account, '525512345678', "$100 por pieza", external_id='IN1')
        self.assertEqual((incoming.res_model, incoming.res_id, incoming.partner_id), ('res.partner', self.vendor.id, self.vendor))
        self.assertEqual(incoming.conversation_id.unread_count, 1)
        self.assertIn('$100', self.vendor.message_ids[0].body)
        self.assertFalse(self.Message._register_incoming(self.account, '525512345678', "$100", external_id='IN1'))

    def test_received_files_are_stored(self):
        incoming = self.Message._register_incoming(
            self.account, '525512345678', "", external_id='IN2', files=[('precio.pdf', 'application/pdf', b'%PDF')],
        )
        self.assertEqual(incoming.attachment_ids.mapped('name'), ['precio.pdf'])
        self.assertEqual(incoming.attachment_ids.raw, b'%PDF')

    def test_conversation_reply_action_prefills_the_composer(self):
        conversation = self._queue().conversation_id
        action = conversation.with_user(self.alice).action_reply()
        self.assertEqual(action['context']['default_phone'], '525512345678')
        self.assertEqual(action['context']['default_account_id'], self.account.id)


@tagged('post_install', '-at_install')
class TestComposerAndTemplates(EvolutionCase):

    def setUp(self):
        super().setUp()
        self.editor = self.env['res.users'].create({
            'name': 'Template Editor', 'login': 'editor_evo',
            'group_ids': [(6, 0, [self.env.ref('base.group_user').id, self.env.ref('mail.group_mail_template_editor').id])],
        })
        self.partner_model = self.env['ir.model']._get('res.partner')
        self.template = self.env['evolution.template'].with_user(self.editor).create({
            'name': 'Saludo', 'model_id': self.partner_model.id, 'body': "Hola {{ object.name }}, ¿cómo va todo?",
        })

    def test_only_editors_write_templates_but_members_render_them(self):
        with self.assertRaises(AccessError):
            self.env['evolution.template'].with_user(self.alice).create({
                'name': 'X', 'model_id': self.partner_model.id, 'body': 'x',
            })
        self.assertEqual(self.template.with_user(self.alice)._render_body(self.vendor), "Hola Proveedor Uno, ¿cómo va todo?")

    def test_partner_action_prefills_account_phone_and_document(self):
        self.account.default_model_ids = self.partner_model
        action = self.vendor.with_user(self.alice).action_send_whatsapp()
        context = action['context']
        self.assertEqual((context['default_res_model'], context['default_res_id']), ('res.partner', self.vendor.id))
        self.assertEqual((context['default_account_id'], context['default_phone']), (self.account.id, self.vendor.phone))

    def test_composer_queues_template_text_and_pdf(self):
        report = self.env['ir.actions.report'].search([('model', '=', 'res.partner'), ('report_type', '=', 'qweb-pdf')], limit=1)
        composer = self.env['evolution.composer'].with_user(self.alice).create({
            'res_model': 'res.partner', 'res_id': self.vendor.id, 'account_id': self.account.id,
            'partner_id': self.vendor.id, 'phone': self.vendor.phone, 'template_id': self.template.id,
            'report_id': report.id, 'attach_document_pdf': bool(report),
        })
        composer._onchange_template_id()
        self.assertEqual(composer.body, "Hola Proveedor Uno, ¿cómo va todo?")
        with patch.object(type(self.env['ir.actions.report']), '_render_qweb_pdf', return_value=(b'%PDF-fake', 'pdf')):
            composer.action_send()
        message = self.Message.search([('account_id', '=', self.account.id)], limit=1)
        self.assertEqual((message.state, message.body, message.author_id), ('queued', composer.body, self.alice))
        if report:
            self.assertEqual(message.attachment_ids.mimetype, 'application/pdf')

    def test_composer_only_offers_usable_accounts(self):
        composer = self.env['evolution.composer'].with_user(self.alice).create({'phone': '5512345678', 'account_id': self.account.id})
        self.assertEqual(composer.allowed_account_ids, self.account)


@tagged('post_install', '-at_install')
class TestWebhook(HttpCase):

    def _post(self, payload, token):
        return self.url_open(f'/evolution_api/webhook?token={token}', data=json.dumps(payload),
                             headers={'Content-Type': 'application/json'})

    def test_webhook_routes_each_token_to_its_account(self):
        vendor = self.env['res.partner'].create({'name': 'Proveedor Web', 'phone': '55 9999 0000'})
        accounts = self.env['evolution.account'].create([
            {'name': f'Account {index}', 'base_url': 'https://evo.example.com', 'instance': f'inst{index}'}
            for index in range(2)
        ])
        first, second = accounts
        event = {
            'event': 'messages.upsert', 'instance': 'inst0',
            'data': {'key': {'remoteJid': '525599990000@s.whatsapp.net', 'fromMe': False, 'id': 'W1'},
                     'message': {'conversation': 'Tengo 50 piezas'}, 'messageType': 'conversation'},
        }
        self.assertEqual(self._post(event, 'wrong-token').status_code, 403)
        self.assertEqual(self.url_open('/evolution_api/webhook').status_code, 405)
        self.assertEqual(self._post(event, first.webhook_token).status_code, 200)
        message = self.env['evolution.message'].search([('external_id', '=', 'W1')])
        self.assertEqual((message.body, message.partner_id, message.account_id), ('Tengo 50 piezas', vendor, first))
        # token of another account with the instance of the first: ignored
        event['data']['key']['id'] = 'W1b'
        self._post(event, second.webhook_token)
        self.assertFalse(self.env['evolution.message'].search([('external_id', '=', 'W1b')]))
        # own messages, groups, other events ignored
        event['instance'] = 'inst0'
        event['data']['key'].update(fromMe=True, id='W2')
        self._post(event, first.webhook_token)
        event['data']['key'].update(fromMe=False, remoteJid='1234@g.us', id='W3')
        self._post(event, first.webhook_token)
        self._post({'event': 'MESSAGES_UPDATE', 'data': {}}, first.webhook_token)
        self.assertFalse(self.env['evolution.message'].search([('external_id', 'in', ('W2', 'W3'))]))
        # uppercase variant of the event name
        event.update(event='MESSAGES_UPSERT')
        event['data']['key'].update(remoteJid='525599990000@s.whatsapp.net', id='W4')
        self._post(event, first.webhook_token)
        self.assertTrue(self.env['evolution.message'].search([('external_id', '=', 'W4')]))

    def test_webhook_media_and_connection_events(self):
        account = self.env['evolution.account'].create({
            'name': 'Media', 'base_url': 'https://evo.example.com', 'instance': 'media',
        })
        self.env['res.partner'].create({'name': 'Proveedor PDF', 'phone': '55 8888 0000'})
        event = {
            'event': 'messages.upsert', 'instance': 'media',
            'data': {'key': {'remoteJid': '525588880000@s.whatsapp.net', 'fromMe': False, 'id': 'M1'},
                     'message': {'documentMessage': {'mimetype': 'application/pdf', 'fileName': 'cotizacion.pdf', 'caption': 'Va la cotización'},
                                 'base64': base64.b64encode(b'%PDF-1.4').decode()},
                     'messageType': 'documentMessage'},
        }
        self.assertEqual(self._post(event, account.webhook_token).status_code, 200)
        message = self.env['evolution.message'].search([('external_id', '=', 'M1')])
        self.assertEqual(message.body, 'Va la cotización')
        self.assertEqual((message.attachment_ids.name, message.attachment_ids.raw), ('cotizacion.pdf', b'%PDF-1.4'))
        self._post({'event': 'connection.update', 'instance': 'media', 'data': {'state': 'close'}}, account.webhook_token)
        self.assertEqual(account.connection_state, 'close')


@tagged('post_install', '-at_install')
class TestConnection(EvolutionCase):

    def test_connection_state_is_read_at_any_depth(self):
        for payload, expected in (({'instance': {'instanceName': 'compras', 'state': 'open'}}, 'open'), ({'state': 'close'}, 'close')):
            with patch(HTTP_PATH, return_value=_fake_response(payload)):
                self.account.action_check_connection()
            self.assertEqual(self.account.connection_state, expected)

    def test_validations(self):
        with self.assertRaises(Exception):
            self.account.write({'min_delay_seconds': 10, 'max_delay_seconds': 5})
        with self.assertRaises(Exception):
            self.env['evolution.account'].create({'name': 'Bad', 'base_url': 'ftp://x', 'instance': 'bad'})


@tagged('post_install', '-at_install')
class TestMigration(TransactionCase):

    def test_single_configuration_becomes_the_first_account(self):
        self.env['evolution.account'].search([]).unlink()
        parameters = self.env['ir.config_parameter'].sudo()
        for name, value in (('base_url', 'https://old.example.com'), ('instance', 'legacy'), ('api_key', 'old-key'),
                            ('webhook_token', 'old-token'), ('enabled', 'True')):
            parameters.set_param(f'evolution_api.{name}', value)
        source = file_open('evolution_api/migrations/19.0.2.0.0/post-migration.py').read()
        namespace = {}
        exec(compile(source, 'post-migration.py', 'exec'), namespace)  # noqa: S102
        namespace['migrate'](self.env.cr, None)
        account = self.env['evolution.account'].search([])
        self.assertEqual((account.instance, account.webhook_token, account.api_key), ('legacy', 'old-token', 'old-key'))
