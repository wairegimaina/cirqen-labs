from .sync_agent_1 import SyncAgent as _A1
from .sync_agent_2 import SyncAgent as _A2
from .sync_agent_3 import SyncAgent as _A3
from .sync_agent_4 import SyncAgent as _A4
from .sync_agent_5 import SyncAgent as _A5
from .sync_agent_6 import SyncAgent as _A6
from .sync_agent_7 import SyncAgent as _A7
from .sync_agent_8 import SyncAgent as _A8
from .sync_agent_9 import SyncAgent as _A9

class SyncAgent(_A1, _A2, _A3, _A4, _A5, _A6, _A7, _A8, _A9):
    pass

__all__ = ["SyncAgent"]
