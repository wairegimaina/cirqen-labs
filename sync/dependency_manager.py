from .agent_prelude import LOG

class DependencyManager:
    """Manages table dependencies for proper sync ordering"""

    def __init__(self, pool):
        self.pool = pool
        self._dependencies_cache = None
        self._fk_relationships_cache = None
        self._sorted_tables_cache = None

    def get_foreign_keys_from_db(self):
        """Fetch all foreign key relationships from database schema"""
        if self._fk_relationships_cache is not None:
            return self._fk_relationships_cache

        query = """
            SELECT
                tc.table_schema,
                tc.table_name,
                kcu.column_name,
                ccu.table_schema AS foreign_table_schema,
                ccu.table_name AS foreign_table_name,
                ccu.column_name AS foreign_column_name
            FROM
                information_schema.table_constraints AS tc
                JOIN information_schema.key_column_usage AS kcu
                  ON tc.constraint_name = kcu.constraint_name
                  AND tc.table_schema = kcu.table_schema
                JOIN information_schema.constraint_column_usage AS ccu
                  ON ccu.constraint_name = tc.constraint_name
                  AND ccu.table_schema = tc.table_schema
            WHERE tc.constraint_type = 'FOREIGN KEY'
            ORDER BY tc.table_schema, tc.table_name;
        """

        conn = None
        try:
            conn = self.pool.getconn()
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute(query)
                self._fk_relationships_cache = cur.fetchall()
                return self._fk_relationships_cache
        except Exception as e:
            LOG.error("Failed to fetch FK relationships: %s", e)
            return []
        finally:
            if conn:
                self.pool.putconn(conn)

    def build_dependency_graph(self, tables):
        """Build dependency graph: child_table -> [parent_tables]"""
        fk_rows = self.get_foreign_keys_from_db()
        dependencies = defaultdict(set)
        fk_details = defaultdict(list)

        for row in fk_rows:
            schema = row['table_schema']
            child = row['table_name']
            fschema = row['foreign_table_schema']
            parent = row['foreign_table_name']
            child_col = row['column_name']
            parent_col = row['foreign_column_name']

            child_full = f"{schema}.{child}"
            parent_full = f"{fschema}.{parent}"

            if child_full in tables:
                dependencies[child_full].add(parent_full)
                fk_details[child_full].append({
                    'parent_table': parent_full,
                    'child_column': child_col,
                    'parent_column': parent_col
                })

        self._dependencies_cache = {k: list(v) for k, v in dependencies.items()}
        return self._dependencies_cache, fk_details

    def discover_table_dependencies_nx(self):
        """Auto-discover dependencies and sort using NetworkX"""
        if not NETWORKX_AVAILABLE:
            LOG.warning("NetworkX not available, using simple ordering")
            return []

        if self._sorted_tables_cache is not None:
            return self._sorted_tables_cache

        conn = None
        try:
            conn = self.pool.getconn()
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT
                        tc.table_schema AS child_schema,
                        tc.table_name AS child_table,
                        ccu.table_schema AS parent_schema,
                        ccu.table_name AS parent_table
                    FROM
                        information_schema.table_constraints AS tc
                    JOIN information_schema.key_column_usage AS kcu
                        ON tc.constraint_name = kcu.constraint_name
                        AND tc.table_schema = kcu.table_schema
                    JOIN information_schema.constraint_column_usage AS ccu
                        ON ccu.constraint_name = tc.constraint_name
                    WHERE
                        tc.constraint_type = 'FOREIGN KEY'
                        AND tc.table_schema = 'public'
                    ORDER BY
                        parent_table, child_table;
                """)

                rows = cur.fetchall()

                G = nx.DiGraph()
                for child_schema, child_table, parent_schema, parent_table in [
                    (r[0], r[1], r[2], r[3]) for r in rows
                ]:
                    parent = f"{parent_schema}.{parent_table}"
                    child = f"{child_schema}.{child_table}"
                    G.add_edge(parent, child)

                try:
                    sorted_tables = list(nx.topological_sort(G))
                    LOG.info("📊 Discovered %d table dependencies", len(sorted_tables))
                    self._sorted_tables_cache = sorted_tables
                    return sorted_tables
                except nx.NetworkXError:
                    LOG.warning("⚠️ Cyclic dependencies detected; using node order")
                    self._sorted_tables_cache = list(G.nodes())
                    return self._sorted_tables_cache

        except Exception as e:
            LOG.error("Failed to discover table dependencies: %s", e)
            return []
        finally:
            if conn:
                self.pool.putconn(conn)

    def sort_updates_by_dependency(self, updates):
        """Sort updates based on table dependencies (parents first)"""
        sorted_tables = self.discover_table_dependencies_nx()

        if not sorted_tables:
            return updates

        table_priority = {table: idx for idx, table in enumerate(sorted_tables)}

        def sort_key(update):
            table = update.get("table")
            priority = table_priority.get(table, 999999)
            return (priority, update.get("row_id", ""))

        return sorted(updates, key=sort_key)

# ---------- Enhanced Sync Agent Class ----------

