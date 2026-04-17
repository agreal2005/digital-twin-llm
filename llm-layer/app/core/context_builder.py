"""
context_builder.py - handles both table and CSV output from Neo4j bridge
"""

import os
import re
import socket
import time
import logging
import sys
from typing import Optional
from datetime import datetime

logging.basicConfig(level=logging.DEBUG, stream=sys.stderr)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
NEO4J_BRIDGE_HOST = os.getenv("NEO4J_BRIDGE_HOST", "127.0.0.1")
NEO4J_BRIDGE_PORT = int(os.getenv("NEO4J_BRIDGE_PORT", "12345"))
NEO4J_BRIDGE_TIMEOUT = int(os.getenv("NEO4J_BRIDGE_TIMEOUT", "15"))

CHROMA_PATH = os.getenv("CHROMA_PATH", "./chroma_store")
CHROMA_COLLECTION = os.getenv("CHROMA_COLLECTION", "network-knowledge")

MODE = os.getenv("CONTEXT_BUILDER_MODE", "live").lower()
print(f"🔧 CONTEXT_BUILDER_MODE = {MODE}", file=sys.stderr)

MAX_CONTEXT_CHARS = 7200
DEVICE_ID_RE = re.compile(r'\b([a-zA-Z]+)[\s-]?(\d+)\b', re.IGNORECASE)

# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------
class TopologyNode:
    def __init__(self, id, type, name, status, ip="", location="", neighbors=None, cpu=None, memory=None):
        self.id = id
        self.type = type
        self.name = name
        self.status = status
        self.ip = ip
        self.location = location
        self.neighbors = neighbors or []
        self.cpu = cpu
        self.memory = memory

class TelemetryData:
    def __init__(self, device_id, metric, value, unit, timestamp=None):
        self.device_id = device_id
        self.metric = metric
        self.value = value
        self.unit = unit
        self.timestamp = timestamp or datetime.now()

class SemanticDoc:
    def __init__(self, id, title, content, relevance_score, category=""):
        self.id = id
        self.title = title
        self.content = content
        self.relevance_score = relevance_score
        self.category = category

class ContextBundle:
    def __init__(self, query, topology=None, telemetry=None, docs=None):
        self.query = query
        self.topology = topology or []
        self.telemetry = telemetry or []
        self.docs = docs or []
        self.blast_radius = []
        self.timestamp = datetime.now()


# ---------------------------------------------------------------------------
# Neo4j Bridge Client - supports CSV and table output
# ---------------------------------------------------------------------------
class Neo4jBridgeClient:
    PROMPT = b"neo4j> "
    ENCODING = "utf-8"

    def __init__(self):
        print(f"🟡 Connecting to Neo4j bridge at {NEO4J_BRIDGE_HOST}:{NEO4J_BRIDGE_PORT}...", file=sys.stderr)
        self._test_connection()
        print(f"✅ Neo4j bridge connected", file=sys.stderr)

    def _test_connection(self):
        sock = None
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(NEO4J_BRIDGE_TIMEOUT)
            sock.connect((NEO4J_BRIDGE_HOST, NEO4J_BRIDGE_PORT))
            self._read_until_prompt(sock)
        except Exception as e:
            print(f"❌ Neo4j bridge test failed: {e}", file=sys.stderr)
            raise
        finally:
            if sock:
                try:
                    sock.sendall(b"exit\n")
                    time.sleep(0.1)
                except Exception:
                    pass
                sock.close()

    def _read_until_prompt(self, sock: socket.socket, timeout: float = 5.0) -> str:
        buf = b""
        sock.settimeout(timeout)
        start_time = time.time()
        while True:
            if time.time() - start_time > timeout:
                break
            try:
                chunk = sock.recv(4096)
            except socket.timeout:
                continue
            except socket.error:
                break
            if not chunk:
                break
            buf += chunk
            if self.PROMPT in buf:
                idx = buf.rfind(self.PROMPT)
                return buf[:idx].decode(self.ENCODING, errors="replace")
        return buf.decode(self.ENCODING, errors="replace")

    def run_query(self, cypher: str) -> str:
        # Flatten query to a single line with single spaces
        flattened = ' '.join(cypher.split())
        if not flattened.endswith(";"):
            flattened += ";"
        print(f"\n📤 [CYPHER QUERY]\n{flattened}\n", file=sys.stderr)
        sock = None
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(NEO4J_BRIDGE_TIMEOUT)
            sock.connect((NEO4J_BRIDGE_HOST, NEO4J_BRIDGE_PORT))
            self._read_until_prompt(sock)
            sock.sendall((flattened + "\n").encode(self.ENCODING))
            # Read until next prompt (includes echoed query)
            response = self._read_until_prompt(sock)
            # Remove the echoed query line if present
            lines = response.splitlines()
            if lines and flattened.strip() in lines[0]:
                response = '\n'.join(lines[1:])
            sock.sendall(b"exit\n")
            time.sleep(0.1)
            print(f"📥 [RESPONSE LENGTH] {len(response)} chars", file=sys.stderr)
            if len(response) < 1000:
                print(f"📥 [RESPONSE]\n{response}\n", file=sys.stderr)
            else:
                print(f"📥 [RESPONSE (first 500)]\n{response[:500]}...\n", file=sys.stderr)
            return response
        except Exception as e:
            print(f"❌ run_query error: {e}", file=sys.stderr)
            raise
        finally:
            if sock:
                sock.close()

    @staticmethod
    def _parse_csv_line(line: str) -> list:
        """Parse a CSV line respecting quotes and brackets."""
        result = []
        current = ""
        in_quotes = False
        in_brackets = 0
        i = 0
        while i < len(line):
            ch = line[i]
            if ch == '"' and not in_brackets:
                in_quotes = not in_quotes
                current += ch
            elif ch == '[' and not in_quotes:
                in_brackets += 1
                current += ch
            elif ch == ']' and not in_quotes and in_brackets > 0:
                in_brackets -= 1
                current += ch
            elif ch == ',' and not in_quotes and in_brackets == 0:
                result.append(Neo4jBridgeClient._parse_cell(current.strip()))
                current = ""
            else:
                current += ch
            i += 1
        if current:
            result.append(Neo4jBridgeClient._parse_cell(current.strip()))
        return result

    @staticmethod
    def _parse_table(raw: str) -> list[dict]:
        """Parse either ASCII table (|) or CSV output into list of dicts."""
        lines = raw.strip().splitlines()
        if not lines:
            return []
        # Check first non-empty line for table format
        first_line = next((l.strip() for l in lines if l.strip()), "")
        if first_line.startswith('|'):
            # --- ASCII table format ---
            header_line = None
            for line in lines:
                stripped = line.strip()
                if stripped.startswith("|") and not stripped.startswith("|--") and not stripped.startswith("+-"):
                    if not any(kw in stripped.lower() for kw in ["match", "return", "where", "cypher"]):
                        header_line = stripped
                        break
            if not header_line:
                print("⚠️ No table header found", file=sys.stderr)
                return []
            columns = [c.strip() for c in header_line.strip("|").split("|") if c.strip()]
            rows = []
            in_data = False
            for line in lines:
                stripped = line.strip()
                if stripped.startswith("|") and not stripped.startswith("|--") and not stripped.startswith("+-"):
                    if not in_data:
                        in_data = True
                        continue
                    if "rows available" in stripped.lower() or "ms" in stripped.lower():
                        continue
                    cells = stripped.strip("|").split("|")
                    if len(cells) != len(columns):
                        continue
                    row = {}
                    for col, cell in zip(columns, cells):
                        row[col] = Neo4jBridgeClient._parse_cell(cell.strip())
                    rows.append(row)
            return rows
        else:
            # --- CSV style output (no header) ---
            rows = []
            for line in lines:
                line = line.strip()
                if not line or line.startswith("rows available") or line.startswith("+"):
                    continue
                values = Neo4jBridgeClient._parse_csv_line(line)
                if values:
                    # Return dict with numeric keys (col0, col1, ...)
                    row = {f"col{i}": val for i, val in enumerate(values)}
                    rows.append(row)
            return rows

    @staticmethod
    def _parse_cell(cell: str):
        c = cell.strip()
        if c in ("<null>", "null", "NULL", ""):
            return None
        if c.startswith("[") and c.endswith("]"):
            inner = c[1:-1].strip()
            if not inner:
                return []
            # Simple split for lists (assumes no nested commas inside quotes)
            items = []
            current = ""
            in_quotes = False
            for ch in inner:
                if ch == '"' and not in_quotes:
                    in_quotes = True
                    current += ch
                elif ch == '"' and in_quotes:
                    in_quotes = False
                    current += ch
                elif ch == ',' and not in_quotes:
                    items.append(Neo4jBridgeClient._parse_cell(current.strip()))
                    current = ""
                else:
                    current += ch
            if current.strip():
                items.append(Neo4jBridgeClient._parse_cell(current.strip()))
            return items
        if (c.startswith('"') and c.endswith('"')) or (c.startswith("'") and c.endswith("'")):
            return c[1:-1]
        try:
            return int(c)
        except ValueError:
            pass
        try:
            return float(c)
        except ValueError:
            pass
        return c

    # ------------------------------------------------------------------
    # Public Methods
    # ------------------------------------------------------------------

    def get_telemetry(self, node_names: Optional[list] = None) -> list[TelemetryData]:
        if node_names and len(node_names) > 0:
            names_str = ", ".join(f'"{n}"' for n in node_names)
            cypher = f"MATCH (n:NetworkNode) WHERE n.name IN [{names_str}] RETURN n.name AS device_id, n.cpu AS cpu, n.memory AS memory, n.packet_loss AS packet_loss, n.bandwidth AS bandwidth, n.latency AS latency"
        else:
            cypher = "MATCH (n:NetworkNode) RETURN n.name AS device_id, n.cpu AS cpu, n.memory AS memory, n.packet_loss AS packet_loss, n.bandwidth AS bandwidth, n.latency AS latency"
        try:
            raw = self.run_query(cypher)
            rows = self._parse_table(raw)
        except Exception as e:
            print(f"❌ get_telemetry failed: {e}", file=sys.stderr)
            return []
        results = []
        now = datetime.now()
        for row in rows:
            device_id = row.get('col0')
            if not device_id:
                continue
            device_id = str(device_id)
            cpu = row.get('col1')
            memory = row.get('col2')
            packet_loss = row.get('col3')
            bandwidth = row.get('col4')
            latency = row.get('col5')
            # Map to TelemetryData
            metrics_map = [
                (cpu, "cpu_usage", "%"),
                (memory, "memory_usage", "%"),
                (packet_loss, "packet_loss", "%"),
                (bandwidth, "bandwidth_mbps", "Mbps"),
                (latency, "latency_ms", "ms"),
            ]
            for value, metric_name, unit in metrics_map:
                if value is not None and str(value).lower() not in ("null", "none", ""):
                    try:
                        numeric_value = float(value)
                        results.append(TelemetryData(
                            device_id=device_id,
                            metric=metric_name,
                            value=round(numeric_value, 2),
                            unit=unit,
                            timestamp=now
                        ))
                    except (ValueError, TypeError):
                        pass
        return results

    def get_topology(self, node_ids: Optional[list] = None) -> list[TopologyNode]:
        """Fetch real topology from Neo4j including neighbor relationships."""
        if node_ids and len(node_ids) > 0:
            ids_str = ", ".join(f'"{d}"' for d in node_ids)
            cypher = f"MATCH (n:NetworkNode) WHERE n.name IN [{ids_str}] OPTIONAL MATCH (n)-[:CONNECTED_TO]-(neighbor:NetworkNode) WITH n, collect(DISTINCT neighbor.name) AS neighbor_names RETURN n.name AS node_id, n.name AS name, n.type AS type, n.status AS status, n.ip_addresses AS ip_addresses, n.cpu AS cpu, n.memory AS memory, neighbor_names AS neighbors"
        else:
            cypher = "MATCH (n:NetworkNode) OPTIONAL MATCH (n)-[:CONNECTED_TO]-(neighbor:NetworkNode) WITH n, collect(DISTINCT neighbor.name) AS neighbor_names RETURN n.name AS node_id, n.name AS name, n.type AS type, n.status AS status, n.ip_addresses AS ip_addresses, n.cpu AS cpu, n.memory AS memory, neighbor_names AS neighbors"
        
        try:
            raw = self.run_query(cypher)
            print(f"📄 RAW RESPONSE:\n{raw}", file=sys.stderr)
            rows = self._parse_table(raw)
            print(f"📊 PARSED ROWS: {rows}", file=sys.stderr)
        except Exception as e:
            print(f"❌ get_topology failed: {e}", file=sys.stderr)
            return []

        nodes = []
        for row in rows:
            node_id = row.get('node_id') or row.get('col0')
            name = row.get('name') or row.get('col1')
            type_ = row.get('type') or row.get('col2')
            status = row.get('status') or row.get('col3')
            ip_addresses = row.get('ip_addresses') or row.get('col4')
            cpu = row.get('cpu') or row.get('col5')
            memory = row.get('memory') or row.get('col6')
            neighbors = row.get('neighbors') or row.get('col7')
            
            node_id = str(node_id) if node_id is not None else ""
            name = str(name) if name is not None else ""
            type_ = str(type_) if type_ is not None else "unknown"
            status = str(status) if status is not None else "unknown"
            
            first_ip = ""
            if isinstance(ip_addresses, list) and ip_addresses:
                first_ip = ip_addresses[0]
            elif isinstance(ip_addresses, str):
                first_ip = ip_addresses
                
            neighbors_list = neighbors if isinstance(neighbors, list) else []
            
            print(f"📍 {name}: neighbors = {neighbors_list}", file=sys.stderr)
            
            nodes.append(TopologyNode(
                id=node_id,
                name=name,
                type=type_,
                status=status,
                ip=first_ip,
                location="",
                neighbors=[str(n) for n in neighbors_list if n],
                cpu=cpu,
                memory=memory,
            ))
        return nodes

    def get_neighbours(self, node_id: str) -> list[str]:
        cypher = f"MATCH (n:NetworkNode {{node_id: \"{node_id}\"}})-[:CONNECTED_TO]->(neighbor:NetworkNode) RETURN neighbor.node_id AS neighbor_id"
        try:
            raw = self.run_query(cypher)
            rows = self._parse_table(raw)
            return [str(r.get('col0')) for r in rows if r.get('col0')]
        except Exception as e:
            print(f"❌ get_neighbours failed: {e}", file=sys.stderr)
            return []

    def get_blast_radius(self, node_id: str) -> list[str]:
        cypher = f"MATCH (n:NetworkNode {{node_id: \"{node_id}\"}})-[:CONNECTED_TO*1..5]->(downstream:NetworkNode) WHERE downstream.node_id <> \"{node_id}\" RETURN DISTINCT downstream.node_id AS affected_id"
        try:
            raw = self.run_query(cypher)
            rows = self._parse_table(raw)
            return [str(r.get('col0')) for r in rows if r.get('col0')]
        except Exception as e:
            print(f"❌ get_blast_radius failed: {e}", file=sys.stderr)
            return []


# ---------------------------------------------------------------------------
# ChromaDB Client (optional)
# ---------------------------------------------------------------------------
class ChromaContextClient:
    def __init__(self):
        try:
            import chromadb
            from chromadb.utils import embedding_functions
            self._client = chromadb.PersistentClient(path=CHROMA_PATH)
            ef = embedding_functions.SentenceTransformerEmbeddingFunction(model_name="all-MiniLM-L6-v2")
            self._collection = self._client.get_or_create_collection(
                name=CHROMA_COLLECTION,
                embedding_function=ef,
                metadata={"hnsw:space": "cosine"},
            )
            print(f"✅ ChromaDB connected ({self._collection.count()} docs)", file=sys.stderr)
        except Exception as e:
            print(f"⚠️ ChromaDB unavailable: {e}", file=sys.stderr)
            self._collection = None

    def search(self, query: str, top_k: int = 3) -> list[SemanticDoc]:
        if not self._collection:
            return []
        try:
            n = max(1, min(top_k, self._collection.count()))
            results = self._collection.query(query_texts=[query], n_results=n)
        except Exception as e:
            print(f"❌ ChromaDB search failed: {e}", file=sys.stderr)
            return []
        docs = []
        for i, doc_text in enumerate(results["documents"][0]):
            meta = results["metadatas"][0][i]
            dist = results["distances"][0][i]
            score = round(1 - dist / 2, 3)
            docs.append(SemanticDoc(
                id=results["ids"][0][i],
                title=meta.get("title", "Unknown"),
                content=doc_text,
                relevance_score=score,
                category=meta.get("category", ""),
            ))
        return docs


# ---------------------------------------------------------------------------
# Context Builder
# ---------------------------------------------------------------------------
class ContextBuilder:
    def __init__(self):
        self._mode = MODE
        self._neo4j = None
        self._chroma = None
        print(f"🔧 Initializing ContextBuilder with MODE={self._mode}", file=sys.stderr)
        if self._mode == "live":
            self._init_live_clients()
        else:
            print("🔧 ContextBuilder running in STUB mode", file=sys.stderr)

    def _init_live_clients(self):
        try:
            self._neo4j = Neo4jBridgeClient()
        except Exception as e:
            print(f"❌ FATAL: Neo4j bridge unavailable: {e}", file=sys.stderr)
            raise RuntimeError("Neo4j is required for live mode. Set CONTEXT_BUILDER_MODE=stub if Neo4j is unavailable.")
        self._chroma = ChromaContextClient()

    def get_topology(self, device_ids: Optional[list] = None) -> list[TopologyNode]:
        if not self._neo4j:
            return []
        return self._neo4j.get_topology(device_ids)

    def get_telemetry(self, device_names: Optional[list] = None) -> list[TelemetryData]:
        if not self._neo4j:
            return []
        return self._neo4j.get_telemetry(device_names)

    def get_semantic_context(self, query: str, top_k: int = 3) -> list[SemanticDoc]:
        if self._chroma:
            return self._chroma.search(query, top_k=top_k)
        return []

    def get_blast_radius(self, device_id: str) -> list[str]:
        if self._neo4j:
            return self._neo4j.get_blast_radius(device_id)
        return []

    def build_context(self, query: str) -> ContextBundle:
        print(f"\n🔍 Building context for: {query[:60]}...", file=sys.stderr)
        matches = DEVICE_ID_RE.findall(query.lower())
        query_device_names = [f"{name}{num}" for name, num in matches]
        print(f"📝 Extracted device names: {query_device_names}", file=sys.stderr)

        all_topology = self.get_topology()
        print(f"📊 Retrieved {len(all_topology)} nodes from Neo4j", file=sys.stderr)

        if query_device_names:
            relevant_nodes = [n for n in all_topology if n.name.lower() in query_device_names]
            if not relevant_nodes:
                for name_part in set([m[0] for m in matches]):
                    relevant_nodes = [n for n in all_topology if name_part in n.name.lower()]
                    if relevant_nodes:
                        break
            topology = relevant_nodes if relevant_nodes else all_topology
        else:
            topology = all_topology

        device_names = [node.name for node in topology if node.name]
        print(f"📡 Requesting telemetry for: {device_names}", file=sys.stderr)
        telemetry = self.get_telemetry(device_names if device_names else None)
        print(f"📈 Retrieved {len(telemetry)} telemetry readings", file=sys.stderr)

        docs = self.get_semantic_context(query)

        blast_radius = []
        for node in topology:
            if node.name and node.name.lower() in query.lower():
                blast_radius.extend(self.get_blast_radius(node.id))
        blast_radius = list(set(blast_radius))

        bundle = ContextBundle(query, topology, telemetry, docs)
        bundle.blast_radius = blast_radius
        print(f"✅ Context built: {len(topology)} nodes, {len(telemetry)} telemetry, {len(docs)} docs, blast_radius={blast_radius}\n", file=sys.stderr)
        return bundle

    def summarize_context(self, bundle: ContextBundle) -> str:
        lines = []
        status_icons = {"active": "✅", "up": "✅", "degraded": "⚠️", "down": "❌", "unknown": "❓"}
        lines.append("=== NETWORK TOPOLOGY ===")
        if not bundle.topology:
            lines.append("No topology data available.")
        else:
            for node in bundle.topology:
                icon = status_icons.get(node.status, "❓")
                lines.append(f"{icon} {node.name} [id: {node.id}] ({node.type}) IP:{node.ip if node.ip else 'N/A'} — {node.status}")
                if node.neighbors:
                    lines.append(f"   Connected to: {', '.join(node.neighbors[:4])}")
        if bundle.blast_radius:
            lines.append(f"\n⚠️ BLAST RADIUS: Affected devices: {', '.join(bundle.blast_radius)}")
        if bundle.telemetry:
            lines.append("\n=== TELEMETRY METRICS ===")
            by_device = {}
            for t in bundle.telemetry:
                by_device.setdefault(t.device_id, []).append(t)
            for device, metrics in by_device.items():
                lines.append(f"\n{device}:")
                for m in metrics:
                    flag = ""
                    if m.metric == "cpu_usage" and m.value > 80:
                        flag = " ⚠️ HIGH"
                    elif m.metric == "memory_usage" and m.value > 85:
                        flag = " ⚠️ HIGH"
                    elif m.metric == "packet_loss" and m.value > 1:
                        flag = " ⚠️ ELEVATED"
                    lines.append(f"   • {m.metric}: {m.value:.1f} {m.unit}{flag}")
        else:
            lines.append("\n=== TELEMETRY ===\n⚠️ No telemetry data available for these devices (metrics not populated in Neo4j)")
        if bundle.docs:
            lines.append("\n=== RELEVANT KNOWLEDGE ===")
            for doc in bundle.docs[:2]:
                lines.append(f"\n[{doc.category.upper()}] {doc.title}")
                content = doc.content[:300] + "..." if len(doc.content) > 300 else doc.content
                lines.append(f"   {content}")
        result = "\n".join(lines)
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