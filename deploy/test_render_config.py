import tempfile
import unittest
from pathlib import Path
from render_config import render


class RenderConfigTests(unittest.TestCase):
    def test_domain_port_and_cert_are_consistent(self):
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder)
            render(Path(__file__).parent, output, 'aa.example.com', 'aa.example.com', 5190)
            nginx = (output/'nginx.conf').read_text()
            service = (output/'travel-aa.service').read_text()
            self.assertIn('proxy_pass http://127.0.0.1:5190;', nginx)
            self.assertIn('server_name aa.example.com;', nginx)
            self.assertIn('live/aa.example.com/fullchain.pem', nginx)
            self.assertIn('--public-host aa.example.com', service)
            self.assertIn('--port 5190', service)
            self.assertNotIn('__PUBLIC_HOST__', nginx + service)
            self.assertNotIn('/srv/travel-aa/current', (output/'nginx-acme.conf').read_text())

    def test_reject_injected_host_certificate_or_port_without_writing(self):
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder)/'output'
            for host, cert, port in [('aa.example.com;return 200;', 'aa',5182),('aa.example.com','../private',5182),('aa.example.com','aa',80)]:
                with self.assertRaises(ValueError):
                    render(Path(__file__).parent, output, host, cert, port)
                self.assertFalse(output.exists())

    def test_documentation_ip_supported(self):
        with tempfile.TemporaryDirectory() as folder:
            render(Path(__file__).parent, Path(folder), '192.0.2.10', 'travel-aa-ip')
            self.assertIn('server_name 192.0.2.10;', (Path(folder)/'nginx.conf').read_text())


if __name__ == '__main__':
    unittest.main()
