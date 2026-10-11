"""Render public deployment templates without storing personal server settings."""
import argparse
import ipaddress
import re
from pathlib import Path


def public_host(value):
    try:
        address = ipaddress.ip_address(value)
    except ValueError:
        labels = value.split('.')
        if len(value) > 253 or not all(re.fullmatch(r'[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?', label) for label in labels):
            raise ValueError('Invalid public hostname')
    else:
        if address.version != 4:
            raise ValueError('Templates currently support IPv4 or DNS hostnames')
    return value


def render(source, destination, host, certificate_name, port=5182):
    public_host(host)
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]{0,63}', certificate_name):
        raise ValueError('Invalid certificate name')
    if not 1024 <= port <= 65535:
        raise ValueError('Backend port must be 1024..65535')
    replacements = {'__PUBLIC_HOST__':host, '__TLS_CERT_NAME__':certificate_name, '__BACKEND_PORT__':str(port)}
    rendered = {}
    for name in ('nginx.conf', 'nginx-acme.conf', 'travel-aa.service'):
        text = (source / name).read_text(encoding='utf-8')
        for token, value in replacements.items():
            text = text.replace(token, value)
        if re.search(r'__[A-Z_]+__', text):
            raise ValueError('Unresolved deployment placeholder')
        rendered[name] = text
    destination.mkdir(parents=True, exist_ok=True)
    for name, text in rendered.items():
        (destination / name).write_text(text, encoding='utf-8')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('destination', type=Path)
    parser.add_argument('--public-host', required=True)
    parser.add_argument('--certificate-name', default='travel-aa-ip')
    parser.add_argument('--port', type=int, default=5182)
    args = parser.parse_args()
    render(Path(__file__).resolve().parent, args.destination, args.public_host, args.certificate_name, args.port)
