"""
context_builder.py
==================
RAG pipeline for the LLM Layer.

Retrieves relevant network context from three live data stores:
  - InfluxDB   : recent telemetry metrics
  - Neo4j      : topology graph (devices, links, paths)
  - ChromaDB   : semantic search over runbooks, configs, incidents

Compresses all retrieved data into a token-budget-aware text block
for the Prompt Engine.

Set CONTEXT_BUILDER_MODE=stub in env to fall back to stub data
(useful when databases are unavailable during development).
"""

import os
import re
import logging
from typing   import Optional
from datetime import datetime, timedelta, timezone

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Config — read from environment (same pattern as config.py)
# ---------------------------------------------------------------------------
INFLUX_URL    = os.getenv("INFLUX_URL",    "http://localhost:8086")
INFLUX_TOKEN  = os.getenv("INFLUX_TOKEN",  "my-super-secret-token")
INFLUX_ORG    = os.getenv("INFLUX_ORG",    "digital-twin")
INFLUX_BUCKET = os.getenv("INFLUX_BUCKET", "network-metrics")

NEO4J_URI      = os.getenv("NEO4J_URI",      "bolt://localhost:7687")
NEO4J_USER     = os.getenv("NEO4J_USER",     "neo4j")
NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD", "password")

CHROMA_PATH       = os.getenv("CHROMA_PATH",       "./chroma_store")
CHROMA_COLLECTION = os.getenv("CHROMA_COLLECTION", "network-knowledge")

# Set to "stub" to bypass real DB calls
MODE = os.getenv("CONTEXT_BUILDER_MODE", "live").lower()

# Token budget: max chars in final context block (~4 chars/token, 1800 token limit)
MAX_CONTEXT_CHARS = 7200

# Regex to extract device IDs like router-1, switch-2, server-3
DEVICE_ID_RE = re.compile(r'\b([a-zA-Z]+-\d+)\b')

# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

class TopologyNode:
    def __init__(self, id, type, name, status, ip="", location="", neighbors=None):
        self.id        = id
        self.type      = type
        self.name      = name
        self.status    = status
        self.ip        = ip
        self.location  = location
        self.neighbors = neighbors or []

class TelemetryData:
    def __init__(self, device_id, metric, value, unit, timestamp=None):
        self.device_id = device_id
        self.metric    = metric
        self.value     = value
        self.unit      = unit
        self.timestamp = timestamp or datetime.now()

class SemanticDoc:
    def __init__(self, id, title, content, relevance_score, category=""):
        self.id              = id
        self.title           = title
        self.content         = content
        self.relevance_score = relevance_score
        self.category        = category

class ContextBundle:
    def __init__(self, query, topology=None, telemetry=None, docs=None):
        self.query     = query
        self.topology  = topology  or []
        self.telemetry = telemetry or []
        self.docs      = docs      or []
        self.timestamp = datetime.now()


# ---------------------------------------------------------------------------
# InfluxDB client
# ---------------------------------------------------------------------------

class InfluxDBContextClient:
    def __init__(self):
        from influxdb_client import InfluxDBClient
        self._client    = InfluxDBClient(url=INFLUX_URL, token=INFLUX_TOKEN, org=INFLUX_ORG)
        self._query_api = self._client.query_api()
        logger.info("✅ InfluxDB client connected")

    def get_recent_metrics(
        self,
        device_ids: Optional[list] = None,
        window_minutes: int = 5,
        metrics: Optional[list] = None,
    ) -> list[TelemetryData]:
        """
        Query the last `window_minutes` of metrics.
        Filters by device_ids if provided.
        """
        # Build optional filter clauses
        device_filter = ""
        if device_ids:
            ids_str = " or ".join(f'r["device_id"] == "{d}"' for d in device_ids)
            device_filter = f'|> filter(fn: (r) => {ids_str})'

        metric_filter = ""
        if metrics:
            fields = " or ".join(f'r["_field"] == "{m}"' for m in metrics)
            metric_filter = f'|> filter(fn: (r) => {fields})'

        flux = f"""
from(bucket: "{INFLUX_BUCKET}")
  |> range(start: -{window_minutes}m)
  |> filter(fn: (r) => r["_measurement"] == "device_metrics")
  {device_filter}
  {metric_filter}
  |> last()
"""
        try:
            tables = self._query_api.query(flux, org=INFLUX_ORG)
        except Exception as e:
            logger.error(f"InfluxDB query failed: {e}")
            return []

        # Unit mapping for fields
        unit_map = {
            "cpu_usage":      "%",
            "memory_usage":   "%",
            "latency_ms":     "ms",
            "packet_loss":    "%",
            "bandwidth_mbps": "Mbps",
            "error_rate":     "err/s",
        }

        results = []
        for table in tables:
            for record in table.records:
                results.append(TelemetryData(
                    device_id = record.values.get("device_id", "unknown"),
                    metric    = record.get_field(),
                    value     = round(float(record.get_value()), 2),
                    unit      = unit_map.get(record.get_field(), ""),
                    timestamp = record.get_time(),
                ))
        return results


# ---------------------------------------------------------------------------
# Neo4j client
# ---------------------------------------------------------------------------

class Neo4jContextClient:
    def __init__(self):
        from neo4j import GraphDatabase
        self._driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))
        logger.info("✅ Neo4j client connected")

    def get_topology(self, device_ids: Optional[list] = None) -> list[TopologyNode]:
        """
        Fetch device nodes and their direct neighbours.
        If device_ids provided, fetch only those devices + their neighbours.
        """
        with self._driver.session() as session:
            if device_ids:
                result = session.run("""
                    MATCH (d:Device)
                    WHERE d.id IN $ids
                    OPTIONAL MATCH (d)-[:CONNECTED_TO]->(n:Device)
                    RETURN d.id AS id, d.name AS name, d.type AS type,
                           d.status AS status, d.ip AS ip, d.location AS location,
                           collect(DISTINCT n.id) AS neighbors
                """, ids=device_ids)
            else:
                result = session.run("""
                    MATCH (d:Device)
                    OPTIONAL MATCH (d)-[:CONNECTED_TO]->(n:Device)
                    RETURN d.id AS id, d.name AS name, d.type AS type,
                           d.status AS status, d.ip AS ip, d.location AS location,
                           collect(DISTINCT n.id) AS neighbors
                """)
            nodes = []
            for row in result:
                nodes.append(TopologyNode(
                    id        = row["id"],
                    name      = row["name"],
                    type      = row["type"],
                    status    = row["status"],
                    ip        = row["ip"] or "",
                    location  = row["location"] or "",
                    neighbors = [n for n in row["neighbors"] if n],
                ))
            return nodes

    def get_neighbours(self, device_id: str) -> list[str]:
        """Return IDs of all direct neighbours of a device."""
        with self._driver.session() as session:
            result = session.run("""
                MATCH (d:Device {id: $id})-[:CONNECTED_TO]->(n:Device)
                RETURN n.id AS neighbour_id
            """, id=device_id)
            return [row["neighbour_id"] for row in result]

    def get_path(self, src_id: str, dst_id: str) -> list[str]:
        """Return shortest path (device IDs) between two devices."""
        with self._driver.session() as session:
            result = session.run("""
                MATCH p = shortestPath(
                    (a:Device {id: $src})-[:CONNECTED_TO*]-(b:Device {id: $dst})
                )
                RETURN [node IN nodes(p) | node.id] AS path
            """, src=src_id, dst=dst_id)
            row = result.single()
            return row["path"] if row else []

    def get_blast_radius(self, device_id: str) -> list[str]:
        """
        Return all devices reachable ONLY through this device.
        Used to assess impact of restarting/shutting down a device.
        """
        with self._driver.session() as session:
            result = session.run("""
                MATCH (d:Device {id: $id})-[:CONNECTED_TO*1..5]->(downstream:Device)
                WHERE downstream.id <> $id
                RETURN DISTINCT downstream.id AS affected_id
            """, id=device_id)
            return [row["affected_id"] for row in result]


# ---------------------------------------------------------------------------
# ChromaDB client
# ---------------------------------------------------------------------------

class ChromaContextClient:
    def __init__(self):
        import chromadb
        from chromadb.utils import embedding_functions
        self._client = chromadb.PersistentClient(path=CHROMA_PATH)
        ef = embedding_functions.SentenceTransformerEmbeddingFunction(
            model_name="all-MiniLM-L6-v2"
        )
        self._collection = self._client.get_or_create_collection(
            name=CHROMA_COLLECTION,
            embedding_function=ef,
            metadata={"hnsw:space": "cosine"},
        )
        logger.info(f"✅ ChromaDB client connected ({self._collection.count()} docs)")

    def search(self, query: str, top_k: int = 3, category: Optional[str] = None) -> list[SemanticDoc]:
        """
        Semantic search over the knowledge base.
        Optionally filter by category: runbook, config, incident, policy, documentation
        """
        where = {"category": category} if category else None
        try:
            n = max(1, min(top_k, self._collection.count()))
            results = self._collection.query(
                query_texts=[query],
                n_results=n,
                where=where,
            )
        except Exception as e:
            logger.error(f"ChromaDB query failed: {e}")
            return []

        docs = []
        for i, doc_text in enumerate(results["documents"][0]):
            meta  = results["metadatas"][0][i]
            dist  = results["distances"][0][i]   # cosine distance: 0=identical, 2=opposite
            score = round(1 - dist / 2, 3)       # convert to 0-1 similarity
            docs.append(SemanticDoc(
                id              = results["ids"][0][i],
                title           = meta.get("title", "Unknown"),
                content         = doc_text,
                relevance_score = score,
                category        = meta.get("category", ""),
            ))
        return docs


# ---------------------------------------------------------------------------
# Stub fallbacks (used when MODE=stub or DB connections fail)
# ---------------------------------------------------------------------------

def _stub_topology(device_id: Optional[str] = None) -> list[TopologyNode]:
    nodes = [
        TopologyNode("router-1", "router",   "Core Router",         "up",       "10.0.0.1",   "core",  ["switch-1","switch-2","fw-1"]),
        TopologyNode("switch-1", "switch",   "Access Switch 1",     "up",       "10.0.1.1",   "core",  ["router-1","server-1","server-2"]),
        TopologyNode("switch-2", "switch",   "Distribution Switch", "degraded", "10.0.1.2",   "core",  ["router-1","server-3"]),
        TopologyNode("server-1", "server",   "Web Server",          "up",       "10.0.2.1",   "dmz",   ["switch-1"]),
        TopologyNode("server-2", "server",   "Database Server",     "up",       "10.0.2.2",   "dmz",   ["switch-1"]),
        TopologyNode("server-3", "server",   "Application Server",  "down",     "10.0.2.3",   "dmz",   ["switch-2"]),
        TopologyNode("fw-1",     "firewall", "Edge Firewall",       "up",       "10.0.0.254", "edge",  ["router-1"]),
    ]
    if device_id:
        return [n for n in nodes if n.id == device_id]
    return nodes

def _stub_telemetry(device_ids: Optional[list] = None) -> list[TelemetryData]:
    from datetime import datetime
    profiles = {
        "router-1": [("cpu_usage",45.6,"%"),("memory_usage",58.2,"%"),("latency_ms",12.5,"ms"),("packet_loss",0.05,"%")],
        "switch-1": [("cpu_usage",18.2,"%"),("memory_usage",28.4,"%"),("latency_ms",3.1,"ms"), ("packet_loss",0.02,"%")],
        "switch-2": [("cpu_usage",52.1,"%"),("memory_usage",61.3,"%"),("latency_ms",6.2,"ms"), ("packet_loss",2.3,"%")],
        "server-1": [("cpu_usage",32.4,"%"),("memory_usage",55.8,"%"),("latency_ms",2.4,"ms"), ("packet_loss",0.01,"%")],
        "server-2": [("cpu_usage",41.7,"%"),("memory_usage",67.2,"%"),("latency_ms",2.1,"ms"), ("packet_loss",0.01,"%")],
        "server-3": [("cpu_usage",98.5,"%"),("memory_usage",95.2,"%"),("latency_ms",0.0,"ms"), ("packet_loss",0.0,"%")],
        "fw-1":     [("cpu_usage",22.3,"%"),("memory_usage",38.1,"%"),("latency_ms",1.8,"ms"), ("packet_loss",0.01,"%")],
    }
    results = []
    now = datetime.now()
    for did, metrics in profiles.items():
        if device_ids and did not in device_ids:
            continue
        for metric, value, unit in metrics:
            results.append(TelemetryData(did, metric, value, unit, now))
    return results

def _stub_docs(query: str) -> list[SemanticDoc]:
    return [
        SemanticDoc("doc-1","Network Troubleshooting Guide",
            "When a server shows degraded status, check CPU and memory usage first.",0.9,"runbook"),
        SemanticDoc("doc-2","Switch Troubleshooting",
            "Switch degradation often indicates spanning tree issues or port flapping.",0.8,"runbook"),
    ]


# ---------------------------------------------------------------------------
# Main ContextBuilder
# ---------------------------------------------------------------------------

class ContextBuilder:
    def __init__(self):
        self._mode = MODE
        self._influx  = None
        self._neo4j   = None
        self._chroma  = None

        if self._mode == "live":
            self._init_live_clients()
        else:
            logger.info("🔧 ContextBuilder running in STUB mode")

    def _init_live_clients(self):
        """
        Try to connect to all three databases.
        Fall back to stub for any that fail — so a missing DB
        doesn't take down the whole service.
        """
        try:
            self._influx = InfluxDBContextClient()
        except Exception as e:
            logger.warning(f"⚠️  InfluxDB unavailable ({e}) — using stub telemetry")

        try:
            self._neo4j = Neo4jContextClient()
        except Exception as e:
            logger.warning(f"⚠️  Neo4j unavailable ({e}) — using stub topology")

        try:
            self._chroma = ChromaContextClient()
        except Exception as e:
            logger.warning(f"⚠️  ChromaDB unavailable ({e}) — using stub docs")

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get_topology(self, device_ids: Optional[list] = None) -> list[TopologyNode]:
        if self._neo4j:
            try:
                return self._neo4j.get_topology(device_ids)
            except Exception as e:
                logger.error(f"Neo4j topology query failed: {e}")
        return _stub_topology(device_ids[0] if device_ids and len(device_ids)==1 else None)

    def get_telemetry(self, device_ids: Optional[list] = None,
                      window_minutes: int = 5) -> list[TelemetryData]:
        if self._influx:
            try:
                return self._influx.get_recent_metrics(
                    device_ids=device_ids,
                    window_minutes=window_minutes,
                )
            except Exception as e:
                logger.error(f"InfluxDB telemetry query failed: {e}")
        return _stub_telemetry(device_ids)

    def get_semantic_context(self, query: str, top_k: int = 3) -> list[SemanticDoc]:
        if self._chroma:
            try:
                return self._chroma.search(query, top_k=top_k)
            except Exception as e:
                logger.error(f"ChromaDB search failed: {e}")
        return _stub_docs(query)

    def get_blast_radius(self, device_id: str) -> list[str]:
        """Return affected devices if this device is restarted/shut down."""
        if self._neo4j:
            try:
                return self._neo4j.get_blast_radius(device_id)
            except Exception as e:
                logger.error(f"Neo4j blast radius query failed: {e}")
        return []

    def build_context(self, query: str) -> ContextBundle:
        logger.info(f"🔍 Building context for: {query[:60]}...")

        # Extract specific device IDs from query (e.g. "router-1", "switch-2")
        device_ids = list(set(DEVICE_ID_RE.findall(query)))

        topology  = self.get_topology(device_ids if device_ids else None)
        telemetry = self.get_telemetry(device_ids if device_ids else None)
        docs      = self.get_semantic_context(query)

        # For action queries targeting a specific device, also fetch blast radius
        blast_radius = []
        if device_ids:
            for did in device_ids:
                blast_radius.extend(self.get_blast_radius(did))

        bundle = ContextBundle(
            query    = query,
            topology = topology,
            telemetry= telemetry,
            docs     = docs,
        )
        # Attach blast radius as an attribute (used by summarize_context)
        bundle.blast_radius = list(set(blast_radius))

        logger.info(
            f"✅ Context: {len(topology)} nodes, {len(telemetry)} metrics, "
            f"{len(docs)} docs, blast_radius={bundle.blast_radius}"
        )
        return bundle

    def summarize_context(self, bundle: ContextBundle) -> str:
        """
        Convert ContextBundle into structured text for the prompt.
        Enforces MAX_CONTEXT_CHARS budget.
        """
        lines = []
        status_icons = {"up": "✅", "degraded": "⚠️", "down": "❌", "unknown": "❓"}

        # ── Topology
        lines.append("=== NETWORK TOPOLOGY ===")
        for node in bundle.topology:
            icon = status_icons.get(node.status, "❓")
            lines.append(
                f"{icon} {node.name} [id: {node.id}] ({node.type}) "
                f"IP:{node.ip} — {node.status}"
            )
            if node.neighbors:
                lines.append(f"   Connected to: {', '.join(node.neighbors[:4])}")

        # ── Blast radius (for action queries)
        if hasattr(bundle, "blast_radius") and bundle.blast_radius:
            lines.append(f"\n⚠️  BLAST RADIUS: Restarting/modifying this device will affect: "
                         f"{', '.join(bundle.blast_radius)}")

        # ── Telemetry
        if bundle.telemetry:
            lines.append("\n=== CURRENT TELEMETRY (last 5 min) ===")
            by_device: dict = {}
            for t in bundle.telemetry:
                by_device.setdefault(t.device_id, []).append(t)

            # Prioritise devices with anomalous values
            def _anomaly_score(metrics):
                score = 0
                for m in metrics:
                    if m.metric == "cpu_usage"    and m.value > 80: score += 2
                    if m.metric == "memory_usage" and m.value > 85: score += 2
                    if m.metric == "packet_loss"  and m.value > 1:  score += 3
                    if m.metric == "error_rate"   and m.value > 0.1:score += 1
                return score

            sorted_devices = sorted(by_device.items(),
                                    key=lambda kv: _anomaly_score(kv[1]),
                                    reverse=True)

            for device, metrics in sorted_devices[:4]:  # max 4 devices
                lines.append(f"\n{device}:")
                # Sort metrics: anomalous first
                for m in sorted(metrics, key=lambda x: x.value, reverse=True)[:5]:
                    flag = ""
                    if m.metric == "cpu_usage"    and m.value > 80: flag = " ⚠️ HIGH"
                    if m.metric == "memory_usage" and m.value > 85: flag = " ⚠️ HIGH"
                    if m.metric == "packet_loss"  and m.value > 1:  flag = " ⚠️ ELEVATED"
                    lines.append(f"   • {m.metric}: {m.value:.1f} {m.unit}{flag}")

        # ── Documentation
        if bundle.docs:
            lines.append("\n=== RELEVANT KNOWLEDGE ===")
            for doc in bundle.docs[:2]:
                lines.append(f"\n[{doc.category.upper()}] {doc.title}")
                # Truncate to 300 chars per doc
                content = doc.content[:300] + "..." if len(doc.content) > 300 else doc.content
                lines.append(f"   {content}")

        result = "\n".join(lines)

        # Hard truncation at budget
        if len(result) > MAX_CONTEXT_CHARS:
            result = result[:MAX_CONTEXT_CHARS] + "\n[context truncated]"

        return result


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------
_context_builder: Optional[ContextBuilder] = None

def get_context_builder() -> ContextBuilder:
    global _context_builder
    if _context_builder is None:
        _context_builder = ContextBuilder()
    return _context_builder