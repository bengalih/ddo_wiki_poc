import requests

from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Test the DDO Wiki API."

    def handle(self, *args, **options):
        url = "https://ddowiki.com/api.php"

        params = {
            "action": "query",
            "meta": "siteinfo",
            "format": "json",
        }

        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/150.0.0.0 Safari/537.36"
            ),
            "Accept": (
                "text/html,application/xhtml+xml,"
                "application/xml;q=0.9,image/avif,image/webp,"
                "*/*;q=0.8"
            ),
            "Accept-Language": "en-US,en;q=0.9",
        }

        response = requests.get(
            url,
            params=params,
            headers=headers,
            timeout=30,
        )

        self.stdout.write(f"HTTP status: {response.status_code}")
        self.stdout.write(
            f"Content-Type: {response.headers.get('Content-Type')}"
        )
        self.stdout.write(f"Content-Length: {len(response.content)}")
        self.stdout.write("")
        self.stdout.write(response.text[:5000])