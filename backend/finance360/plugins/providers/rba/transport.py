"""The only code that talks to the RBA, behind a one-function seam (``FetchCsv``).

Tests never reach the network: they pass a fake. The RBA publishes this table openly; we ask for
it at most once per run, with a plain identifying User-Agent and a timeout.
"""

from __future__ import annotations

import urllib.error
import urllib.request
from collections.abc import Callable

from finance360.capabilities.reference.providers import ProviderError, ProviderThrottled

URL = "https://www.rba.gov.au/statistics/tables/csv/f11.1-data.csv"
TIMEOUT_SECONDS = 30
#: The table is about 150 kB; refuse anything absurd rather than read it into memory.
MAX_BYTES = 10_000_000
USER_AGENT = "Finance360 (personal household finance app)"

FetchCsv = Callable[[], bytes]


def fetch_f11() -> bytes:
    if not URL.startswith("https://"):  # pragma: no cover - a constant, guarded for S310
        raise ProviderError("The RBA address must be https.")
    request = urllib.request.Request(URL, headers={"User-Agent": USER_AGENT})  # noqa: S310
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:  # noqa: S310
            data = response.read(MAX_BYTES + 1)
    except urllib.error.HTTPError as error:
        if error.code == 429:
            raise ProviderThrottled("The RBA is rate limiting requests.") from None
        raise ProviderError(f"The RBA returned HTTP {error.code}.") from None
    except (urllib.error.URLError, TimeoutError, OSError) as error:
        raise ProviderError(f"The RBA could not be reached ({type(error).__name__}).") from None
    if len(data) > MAX_BYTES:
        raise ProviderError("The RBA table was larger than expected.")
    return data
