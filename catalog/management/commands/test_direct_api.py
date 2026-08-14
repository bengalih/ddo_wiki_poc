import json

import requests

from django.core.management.base import BaseCommand


API_URL = "https://ddowiki.com/api.php"


class Command(BaseCommand):
    help = "Test direct HTTP access to the DDO Wiki API."

    def handle(self, *args, **options):
        session = requests.Session()

        session.headers.update({
            "User-Agent": (
                "DDOWikiCatalog/1.0 "
                "(local development test)"
            ),
            "Accept": "application/json",
        })

        self.stdout.write("Testing direct API access...")

        params = {
            "action": "query",
            "titles": "Item:Stormreach Guardian's Hammer",
            "prop": "info",
            "format": "json",
        }

        response = session.get(
            API_URL,
            params=params,
            timeout=30,
        )

        self.stdout.write(
            f"HTTP status: {response.status_code}"
        )

        self.stdout.write("")
        self.stdout.write(response.text)

        if response.status_code != 200:
            self.stdout.write(
                self.style.ERROR(
                    "Direct API request failed."
                )
            )
            return

        try:
            data = response.json()
        except ValueError:
            self.stdout.write(
                self.style.ERROR(
                    "Response was not JSON."
                )
            )
            return

        if "error" in data:
            self.stdout.write(
                self.style.ERROR(
                    f"API ERROR: {data['error']}"
                )
            )
            return

        self.stdout.write("")
        self.stdout.write(
            self.style.SUCCESS(
                "Direct API access works."
            )
        )