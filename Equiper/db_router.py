"""
Database router for the CMMS + Calibration system.

Offline-first policy:
    * Reads  → ALWAYS local ("default").
    * Writes → ALWAYS local ("default"). While HQ is online the rows each
      request saved are pushed to HQ's sync API right away (core.hq_link);
      otherwise the sync agent uploads them when HQ is reachable again.
    * Migrations → applied on BOTH local and HQ to keep schemas identical.

Why reads are always local
---------------------------
An earlier version routed reads of records older than 30 days to the HQ
(Render) database when "online". That was removed because it was both broken
and harmful:

  * It keyed off ``hints["instance"]``, which Django almost never supplies to
    ``db_for_read`` for querysets — so the branch rarely fired at all.
  * When it did fire it issued a synchronous, transatlantic, SSL read to Render
    on the UI request path (slow, and a failure point on flaky links).
  * It broke read-your-writes: a record is written locally but, once older than
    30 days, would be read from HQ — which may not have the latest local edit
    until the next sync.

Each client already holds a full local replica (via bootstrap/mirror + ongoing
sync), so there is nothing to gain by reading from HQ. Keeping every read local
is the whole point of an offline-first design.
"""


class EquiperDatabaseRouter:

    # 📖 Reads — always local.
    def db_for_read(self, model, **hints):
        return "default"

    # ✏️ Writes — always local; core.hq_link or the sync agent pushes them to HQ.
    def db_for_write(self, model, **hints):
        return "default"

    # 🔗 Allow relations between objects from either configured DB.
    def allow_relation(self, obj1, obj2, **hints):
        db_list = ("default", "hq")
        if obj1._state.db in db_list and obj2._state.db in db_list:
            return True
        return None

    # 🧱 Apply migrations on both local and HQ to keep schemas identical.
    def allow_migrate(self, db, app_label, model_name=None, **hints):
        return db in ("default", "hq")
