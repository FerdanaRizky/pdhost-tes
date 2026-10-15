import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from agent import agent


class SSLManagerTests(unittest.TestCase):
    def test_ssl_config_redirects_http_and_uses_tls(self):
        config = agent.site_config_ssl('example.com', '/var/www/openhost-sites/example.com/public', '/run/php/php-fpm.sock')
        self.assertIn('listen 443 ssl;', config)
        self.assertIn('return 301 https://$host$request_uri;', config)
        self.assertIn('/etc/letsencrypt/live/example.com/fullchain.pem', config)
        self.assertIn('location ^~ /.well-known/acme-challenge/', config)

    def test_ssl_config_rejects_invalid_domain(self):
        result = agent.ssl_status('invalid_domain')
        self.assertFalse(result['ok'])

    @patch.object(agent.shutil, 'which', return_value='/usr/bin/certbot')
    def test_enable_ssl_rejects_invalid_email_before_running_certbot(self, which):
        result = agent.enable_ssl('example.com', 'not-an-email')
        self.assertFalse(result['ok'])
        self.assertIn('email', result['error'].lower())

    def test_http_config_keeps_acme_challenge_reachable(self):
        config = agent.site_config('example.com', '/var/www/openhost-sites/example.com/public', '/run/php/php-fpm.sock')
        self.assertIn('location ^~ /.well-known/acme-challenge/', config)


if __name__ == '__main__':
    unittest.main()
