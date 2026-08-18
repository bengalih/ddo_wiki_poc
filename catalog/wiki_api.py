import json
import random
import time
from pathlib import Path
from urllib.parse import urlencode

import requests
from django.conf import settings
from playwright.sync_api import sync_playwright

API_URL = "https://ddowiki.com/api.php"
WIKI_HOME = "https://ddowiki.com/"

# MediaWiki requires a descriptive user agent. Consider adding
# contact information per their bot policy.
USER_AGENT = "DDOItemIndex/0.2 (personal project)"

# WAF token (AWS WAF challenge solved by a headless browser).
WAF_TOKEN_FILE = Path(settings.BASE_DIR) / "wiki_waf_token.json"
WAF_TOKEN_REUSE_SECONDS = 600

# Pacing and retry policy.
REQUEST_DELAY_SECONDS = 1.0
REQUEST_JITTER_SECONDS = 0.2
MAX_BACKOFF_SECONDS = 60
MAX_RETRIES = 5
MAXLAG = 5


class WikiAPI:
    def __init__(self):
        self.session = requests.Session()
        self._waf_token = None
        self._waf_expires = 0
        self.stdout = None
        self.stderr = None

    @staticmethod
    def build_url(params):
        """Build the full API URL from a params dict."""
        query = dict(params)
        query.setdefault("maxlag", str(MAXLAG))
        return f"{API_URL}?{urlencode(query)}"

    def waf_token(self, force=False):
        now = time.time()

        if (
            not force
            and self._waf_token
            and self._waf_expires > now + WAF_TOKEN_REUSE_SECONDS
        ):
            return self._waf_token

        if not force:
            cached = self.load_waf_token()

            if (
                cached
                and cached["expires"] > now + WAF_TOKEN_REUSE_SECONDS
            ):
                self._waf_token = cached["token"]
                self._waf_expires = cached["expires"]
                self.set_session_token(cached["token"])

                return cached["token"]

        if self.stdout:
            self.stdout.write(
                "Solving WAF challenge with a headless browser..."
            )

        token, expires = self.solve_waf_token()

        self._waf_token = token
        self._waf_expires = expires
        self.save_waf_token(token, expires)
        self.set_session_token(token)

        return token

    def solve_waf_token(self):
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context()
            page = context.new_page()

            try:
                page.goto(
                    WIKI_HOME,
                    wait_until="domcontentloaded",
                    timeout=60000,
                )

                token_cookie = None
                deadline = time.time() + 45

                while time.time() < deadline:
                    page.wait_for_timeout(2000)

                    try:
                        response = page.request.get(
                            API_URL,
                            params={
                                "action": "query",
                                "meta": "siteinfo",
                                "format": "json",
                            },
                        )
                    except Exception:
                        continue

                    if response.status != 200:
                        continue

                    cookies = context.cookies()

                    token_cookie = next(
                        (
                            cookie
                            for cookie in cookies
                            if cookie["name"] == "aws-waf-token"
                        ),
                        None,
                    )

                    if token_cookie:
                        break

                if not token_cookie:
                    raise RuntimeError(
                        "WAF challenge could not be solved "
                        "within the timeout."
                    )
            finally:
                browser.close()

        expires = token_cookie.get("expires", -1)

        if expires is None or expires < 0:
            expires = time.time() + 2 * 3600

        return token_cookie["value"], expires

    def load_waf_token(self):
        if not WAF_TOKEN_FILE.exists():
            return None

        try:
            with WAF_TOKEN_FILE.open("r", encoding="utf-8") as f:
                data = json.load(f)

            if data.get("token") and data.get("expires"):
                return data

            return None

        except (OSError, ValueError, json.JSONDecodeError):
            return None

    def save_waf_token(self, token, expires):
        temp_file = WAF_TOKEN_FILE.with_suffix(".tmp")

        data = {"token": token, "expires": expires}

        with temp_file.open("w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)

        temp_file.replace(WAF_TOKEN_FILE)

    def set_session_token(self, token):
        self.session.cookies.set(
            "aws-waf-token",
            token,
            domain="ddowiki.com",
            path="/",
        )

    def api_request(self, params):
        query = dict(params)
        query.setdefault("maxlag", str(MAXLAG))

        for attempt in range(1, MAX_RETRIES + 1):
            token = self.waf_token()

            try:
                response = self.session.get(
                    API_URL,
                    params=query,
                    headers={"User-Agent": USER_AGENT},
                    timeout=30,
                )
            except requests.RequestException as exc:
                if attempt == MAX_RETRIES:
                    raise RuntimeError(
                        f"Request failed after {MAX_RETRIES} "
                        f"attempts: {exc}"
                    ) from exc

                if self.stdout:
                    self.stdout.write(
                        f"Request failed ({exc}); retrying..."
                    )

                time.sleep(self.backoff(attempt))
                continue

            if response.status_code == 202:
                if attempt < MAX_RETRIES:
                    if self.stderr:
                        self.stderr.write(
                            f"WAF 202; "
                            f"body={response.text[:200]}; "
                            f"refreshing..."
                        )

                    self.waf_token(force=True)
                    time.sleep(self.backoff(attempt))
                    continue

                raise RuntimeError(
                    "WAF challenge could not be solved."
                )

            if response.status_code == 403:
                if attempt < MAX_RETRIES:
                    if self.stderr:
                        self.stderr.write(
                            f"HTTP 403; retrying "
                            f"(attempt {attempt}/{MAX_RETRIES})..."
                        )

                    time.sleep(self.backoff(attempt))
                    continue

                raise RuntimeError(
                    f"HTTP 403 after {MAX_RETRIES} attempts."
                )

            if response.status_code == 200:
                try:
                    data = response.json()
                except ValueError:
                    if attempt == MAX_RETRIES:
                        raise RuntimeError(
                            "API returned invalid JSON."
                        )

                    time.sleep(self.backoff(attempt))
                    continue

                error = data.get("error")

                if error:
                    if error.get("code") == "maxlag":
                        if attempt == MAX_RETRIES:
                            raise RuntimeError(
                                "Wiki lag did not clear."
                            )

                        if self.stdout:
                            self.stdout.write(
                                "Wiki is lagging; backing off..."
                            )

                        time.sleep(self.backoff(attempt))
                        continue

                    raise RuntimeError(f"API ERROR: {error}")

                self.pace_sleep()

                return data

            if response.status_code in (429, 500, 502, 503, 504):
                retry_after = response.headers.get("Retry-After")

                wait = (
                    float(retry_after)
                    if retry_after
                    else self.backoff(attempt)
                )

                if self.stdout:
                    self.stdout.write(
                        f"HTTP {response.status_code}; "
                        f"waiting {wait:.1f}s..."
                    )

                if attempt == MAX_RETRIES:
                    raise RuntimeError(
                        f"HTTP {response.status_code} after "
                        f"{MAX_RETRIES} attempts."
                    )

                time.sleep(wait)
                continue

            raise RuntimeError(
                f"Unexpected HTTP {response.status_code} "
                "from the wiki."
            )

        raise RuntimeError("API retries exhausted.")

    def backoff(self, attempt):
        return (
            min(2 ** attempt, MAX_BACKOFF_SECONDS)
            + random.uniform(0, REQUEST_JITTER_SECONDS)
        )

    def pace_sleep(self):
        time.sleep(
            REQUEST_DELAY_SECONDS
            + random.uniform(0, REQUEST_JITTER_SECONDS)
        )

    def close(self):
        self.session.close()
