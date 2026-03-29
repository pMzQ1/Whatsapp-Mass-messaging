# WhatsApp Batch Sender

This tool sends individual WhatsApp Web messages to recipients from a CSV file.

## Files
- `send_whatsapp.py`: main script
- `recipients.csv`: input file (required)
- `Message.txt`: message template; use `{name}` anywhere to personalize per recipient
- `preview.csv`: generated validation preview
- `send_log.csv`: generated send/resume log
- `.whatsapp_profile/`: Playwright browser profile for persistent login
- `config.json`: optional runtime config (copy from `config.example.json`)

## Required CSV Format
Your CSV must include a header row with these exact columns:
- `name`
- `phone`

Example:

```csv
name,phone
Jan,+31612345678
Piet,+32470111222
```

Phone numbers must be strict E.164 (`+` + country code + digits).

## Install
```bash
pip install -r requirements.txt
python -m playwright install chromium
```

## Run
1. Dry-run validation only:
```bash
python send_whatsapp.py --dry-run
```

2. Test send to first 2 valid recipients:
```bash
python send_whatsapp.py --test-send
```

3. Full send (resumes, skips already `sent` numbers):
```bash
python send_whatsapp.py --full-send
```

## Notes
- First browser run may require QR login in WhatsApp Web.
- The script reads message text from `Message.txt` by default.
- Use `--message-file <path>` to use a different message file.
- Use `{name}` in the message text to insert the recipient name from `recipients.csv`.
- Invalid rows stop sending and must be fixed first.
