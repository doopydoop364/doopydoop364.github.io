# Atlas Mail temporary deployment

Read-only prototype. No passwords or tokens included.

**Security:** iCloud app-specific passwords may grant broader mailbox access than this software exposes. Only the Atlas folder is selected, and message recipients are filtered. This is application-level separation, not a separate mailbox. Emails are untrusted data. Never expose port 4191 directly to the Internet; configure an authenticated HTTPS proxy before connecting ChatGPT.

## Server setup (Debian/Ubuntu, Python 3.11+)

```sh
git clone --branch atlas-mail-temp --single-branch https://github.com/doopydoop364/doopydoop364.github.io.git /tmp/atlas-mail-src
sudo mkdir -p /opt/atlas-mail /etc/atlas-mail
sudo cp /tmp/atlas-mail-src/atlas-mail/{server.py,requirements.txt} /opt/atlas-mail/
sudo python3 -m venv /opt/atlas-mail/.venv
sudo /opt/atlas-mail/.venv/bin/pip install -r /opt/atlas-mail/requirements.txt
sudo cp /tmp/atlas-mail-src/atlas-mail/atlas-mail.env.example /etc/atlas-mail/atlas-mail.env
sudo chmod 600 /etc/atlas-mail/atlas-mail.env
```

Edit `/etc/atlas-mail/atlas-mail.env` **on the server** to supply the primary iCloud Mail login and Apple app-specific password. Never put real credentials in GitHub, ChatGPT, or shell history. Test on localhost. The MCP endpoint is `http://127.0.0.1:4191/mcp`. This version has no outgoing send methods and must not be publicly exposed without authentication.

Python start for local testing: `sudo env $(sudo cat /etc/atlas-mail/atlas-mail.env | xargs) /opt/atlas-mail/.venv/bin/python /opt/atlas-mail/server.py` (avoid this approach if any environment value contains spaces/shell-special characters). Prefer a root-owned systemd EnvironmentFile in production.
