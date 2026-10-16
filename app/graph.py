"""Read-only Graph calendarView client. Raw responses/tokens are never logged."""
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import quote, urlsplit
from threading import Lock

from app.schedule import normalize_event

SELECT = "id,subject,start,end,isCancelled,isAllDay,showAs,sensitivity,organizer"


class GraphError(Exception):
    def __init__(self, code, retry_after=60):
        super().__init__(code)
        self.code = code
        self.retry_after = retry_after


class MsalTokenProvider:
    def __init__(self, credentials):
        self.credentials = credentials
        self.application = None
        self.lock = Lock()

    def __call__(self, force_refresh=False):
        # One MSAL instance/token cache is shared by all room workers.
        with self.lock:
            return self._acquire(force_refresh)

    def _acquire(self, force_refresh=False):
        try:
            if self.application is None:
                import msal
                self.application = msal.ConfidentialClientApplication(
                    self.credentials.client_id,
                    authority="https://login.microsoftonline.com/" + self.credentials.tenant_id,
                    client_credential=self.credentials.client_secret,
                    timeout=10,
                )
            if force_refresh:
                self.application.remove_tokens_for_client()
            result = self.application.acquire_token_for_client(scopes=["https://graph.microsoft.com/.default"])
            token = result.get("access_token")
            if not isinstance(token, str) or not token:
                raise GraphError("authentication")
            return token
        except ImportError:
            raise GraphError("dependencies") from None
        except Exception:
            raise GraphError("authentication") from None


def retry_delay(value):
    try:
        return max(60, int(value))
    except (ValueError, TypeError):
        try:
            return max(60, int((parsedate_to_datetime(value) - datetime.now(timezone.utc)).total_seconds()) + 1)
        except (TypeError, ValueError, OverflowError):
            return 60


class GraphClient:
    def __init__(self, room_upn, token_provider, session=None):
        self.room_upn = room_upn
        self.token_provider = token_provider
        self.session = session

    def fetch(self, start, end):
        try:
            return self._fetch(start, end)
        except GraphError:
            raise
        except ImportError:
            raise GraphError("dependencies") from None
        except Exception:
            # Do not expose request URLs, response bodies, subjects or credentials.
            raise GraphError("communication") from None

    def _fetch(self, start, end):
        if self.session is None:
            import requests
            self.session = requests.Session()
        token = self.token_provider()
        url = "https://graph.microsoft.com/v1.0/users/" + quote(self.room_upn, safe="") + "/calendarView"
        params = {"startDateTime": start.isoformat(), "endDateTime": end.isoformat(), "$select": SELECT}
        events = {}
        seen = set()
        refreshed = False
        while url:
            parsed = urlsplit(url)
            if (parsed.scheme != "https" or parsed.hostname != "graph.microsoft.com" or
                    parsed.port not in (None, 443) or parsed.username or parsed.password or
                    parsed.fragment or not parsed.path.startswith("/v1.0/") or url in seen or len(seen) >= 1000):
                raise GraphError("invalid_response")
            seen.add(url)
            while True:
                response = self.session.get(url, params=params,
                    headers={"Authorization": "Bearer " + token, "Prefer": 'outlook.timezone="UTC"'},
                    timeout=(10, 20), allow_redirects=False)
                try:
                    status = response.status_code
                    if status == 401 and not refreshed:
                        token = self.token_provider(force_refresh=True)
                        refreshed = True
                        continue
                    if status != 200:
                        code = "authentication" if status == 401 else "permission" if status == 403 else "throttled" if status == 429 else "http"
                        raise GraphError(code, retry_delay(response.headers.get("Retry-After")))
                    try:
                        body = response.json()
                        if not isinstance(body, dict) or not isinstance(body.get("value"), list):
                            raise ValueError("invalid response")
                        for raw in body["value"]:
                            event = normalize_event(raw)
                            if event is not None:
                                if event.id in events and events[event.id] != event:
                                    raise ValueError("conflicting duplicate")
                                events[event.id] = event
                        next_url = body.get("@odata.nextLink")
                        if "@odata.nextLink" in body and (not isinstance(next_url, str) or not next_url):
                            raise ValueError("invalid nextLink")
                    except (ValueError, TypeError, KeyError, AttributeError):
                        raise GraphError("invalid_response") from None
                finally:
                    response.close()
                break
            url, params = next_url, None
        return tuple(events.values())
