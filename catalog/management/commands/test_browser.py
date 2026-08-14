from django.core.management.base import BaseCommand
from playwright.sync_api import sync_playwright


class Command(BaseCommand):
    help = "Test enumerating the DDO Wiki Item namespace."

    def handle(self, *args, **options):
        api_url = (
            "https://ddowiki.com/api.php"
            "?action=query"
            "&list=allpages"
            "&apnamespace=500"
            "&aplimit=10"
            "&format=json"
        )

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=False)
            context = browser.new_context()
            page = context.new_page()

            self.stdout.write("Opening DDO Wiki...")

            page.goto(
                "https://ddowiki.com/",
                wait_until="domcontentloaded",
                timeout=60000,
            )

            self.stdout.write("Waiting for WAF challenge...")
            page.wait_for_timeout(10000)

            self.stdout.write(
                "Enumerating Item namespace..."
            )

            response = page.request.get(api_url)

            self.stdout.write(
                f"HTTP status: {response.status}"
            )
            self.stdout.write("")

            data = response.json()

            if "error" in data:
                self.stdout.write(
                    f"API ERROR: {data['error']}"
                )
            else:
                pages = data.get(
                    "query", {}
                ).get(
                    "allpages", []
                )

                self.stdout.write(
                    f"Pages returned: {len(pages)}"
                )
                self.stdout.write("")

                for item in pages:
                    self.stdout.write(
                        f"{item['pageid']}  {item['title']}"
                    )

                if "continue" in data:
                    self.stdout.write("")
                    self.stdout.write(
                        "More pages are available."
                    )
                    self.stdout.write(
                        f"Continuation: {data['continue']}"
                    )

            browser.close()