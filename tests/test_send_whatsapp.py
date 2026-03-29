import unittest

from send_whatsapp import Recipient, build_preview, load_csv_rows, render_message_for_recipient, validate_e164


class SenderValidationTests(unittest.TestCase):
    def test_validate_e164(self):
        self.assertTrue(validate_e164('+31612345678'))
        self.assertFalse(validate_e164('0612345678'))
        self.assertFalse(validate_e164('+0123456789'))

    def test_build_preview_deduplicates_valid_numbers(self):
        rows = [
            {'row_number': '2', 'name': 'A', 'phone': '+31611111111'},
            {'row_number': '3', 'name': 'B', 'phone': '+31611111111'},
        ]
        preview, valid_unique, invalid_count = build_preview(rows)
        self.assertEqual(invalid_count, 0)
        self.assertEqual(len(valid_unique), 1)
        self.assertEqual(preview[1].status, 'duplicate')

    def test_load_csv_rows_requires_name_phone_header(self):
        from pathlib import Path
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'r.csv'
            path.write_text('first,second\nA,+31612345678\n', encoding='utf-8')
            with self.assertRaises(ValueError):
                load_csv_rows(path)

    def test_render_message_for_recipient_replaces_name_token(self):
        recipient = Recipient(name='Jan', phone='+31612345678')
        rendered = render_message_for_recipient('Beste {name},\nWelkom!', recipient)
        self.assertEqual(rendered, 'Beste Jan,\nWelkom!')


if __name__ == '__main__':
    unittest.main()
