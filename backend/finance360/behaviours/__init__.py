"""Account Behaviours (simple_balance, holdings_based, ...). Core never imports this package.

``REGISTRY`` maps a Behaviour key to the Behaviour. Account types store only the key (a plain
string); the app checks at startup that every stored key resolves. A Behaviour may carry a derive
hook (``holdings_based`` does); the Position service uses it.
"""

from finance360.behaviours.holdings_based import HOLDINGS_BASED
from finance360.behaviours.simple_balance import SIMPLE_BALANCE
from finance360.behaviours.types import Behaviour

REGISTRY: dict[str, Behaviour] = {b.key: b for b in (SIMPLE_BALANCE, HOLDINGS_BASED)}

__all__ = ["REGISTRY", "Behaviour"]
