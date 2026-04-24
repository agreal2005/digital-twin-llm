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
class TopologyLink:
    """Represents a CONNECTED_TO relationship between two nodes."""
    def __init__(self, link_id, from_device, to_device, from_ip, to_ip, delay, data_rate):
        self.link_id = link_id
        self.from_device = from_device
        self.to_device = to_device
        self.from_ip = from_ip
        self.to_ip = to_ip
        self.delay = delay          # e.g. "1ms", "2ms"
        self.data_rate = data_rate  # e.g. "5Gbps", "10Gbps"

class TopologyNode:
    def __init__(self, id, type, name, status, ip="", location="", ns3_ips=None, neighbors=None, links=None, cpu=None, memory=None):
        self.id = id
        self.type = type
        self.name = name
        self.status = status
        self.ip = ip
        self.location = location
        self.ns3_ips = ns3_ips or []
        self.neighbors = neighbors or []
        self.links = links or []    # list of TopologyLink objects
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
    def __init__(self, query, topology=None, telemetry=None, docs=None, links=None):
        self.query = query
        self.topology = topology or []
        self.telemetry = telemetry or []
        self.docs = docs or []
        self.links = links or []
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
            response = self._read_until_prompt(sock)
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
        first_line = next((l.strip() for l in lines if l.strip()), "")
        if first_line.startswith('|'):
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
            rows = []
            for line in lines:
                line = line.strip()
                if not line or line.startswith("rows available") or line.startswith("+"):
                    continue
                values = Neo4jBridgeClient._parse_csv_line(line)
                if values:
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

    def get_topology_with_links(self, node_names: Optional[list] = None) -> tuple[list[dict], list[dict]]:
        """
        Single Cypher query that returns both nodes AND links in one shot.
        Returns: (node_rows, link_rows)
        """
        if node_names and len(node_names) > 0:
            names_str = ", ".join(f'"{n}"' for n in node_names)
            where_clause = f"WHERE n.name IN [{names_str}]"
        else:
            where_clause = ""

        cypher = (
            f"MATCH (n:NetworkNode) {where_clause} "
            f"OPTIONAL MATCH (n)-[r:CONNECTED_TO]-(m:NetworkNode) "
            f"RETURN n.name AS node_name, n.type AS type, n.ip AS ip, n.location AS location, "
            f"n.ns3_ips AS ns3_ips, n.id AS node_id, "
            f"collect(DISTINCT m.name) AS neighbors, "
            f"collect(DISTINCT {{id: r.id, from_ip: r.from_interface_ip, to_ip: r.to_interface_ip, "
            f"delay: r.delay, data_rate: r.data_rate, other_name: m.name}}) AS link_data"
        )

        try:
            raw = self.run_query(cypher)
            rows = self._parse_table(raw)
        except Exception as e:
            print(f"❌ get_topology_with_links failed: {e}", file=sys.stderr)
            return [], []

        node_rows = []
        link_rows = []
        seen_links = set()

        for row in rows:
            # Extract node fields
            node_name = str(row.get('node_name') or row.get('col0') or '')
            node_rows.append({
                'name': node_name,
                'type': str(row.get('type') or row.get('col1') or 'unknown'),
                'ip': str(row.get('ip') or row.get('col2') or ''),
                'location': str(row.get('location') or row.get('col3') or ''),
                'ns3_ips': row.get('ns3_ips') or row.get('col4') or [],
                'node_id': str(row.get('node_id') or row.get('col5') or ''),
                'neighbors': row.get('neighbors') or row.get('col6') or [],
            })

            # Extract link data
            link_data_list = row.get('link_data') or row.get('col7') or []
            if isinstance(link_data_list, list):
                for link in link_data_list:
                    if link is None:
                        continue
                    # link could be dict or list depending on bridge output format
                    if isinstance(link, dict):
                        other = link.get('other_name', '')
                        link_id = link.get('id')
                        from_ip = link.get('from_ip', '')
                        to_ip = link.get('to_ip', '')
                        delay = link.get('delay', '')
                        data_rate = link.get('data_rate', '')
                    elif isinstance(link, list):
                        # Just skip malformed entries
                        continue
                    else:
                        continue

                    if other and node_name and link_id is not None:
                        # Deduplicate: only add each link once
                        pair = tuple(sorted([node_name, str(other)]))
                        if pair not in seen_links:
                            seen_links.add(pair)
                            link_rows.append({
                                'link_id': str(link_id),
                                'from_device': node_name,
                                'to_device': str(other),
                                'from_ip': str(from_ip) if from_ip else '',
                                'to_ip': str(to_ip) if to_ip else '',
                                'delay': str(delay) if delay else 'unknown',
                                'data_rate': str(data_rate) if data_rate else 'unknown',
                            })

        return node_rows, link_rows

    def get_topology(self, node_ids: Optional[list] = None) -> list[TopologyNode]:
        """Fetch real topology from Neo4j including neighbor relationships and links."""
        node_names = node_ids if node_ids else None
        node_rows, link_rows = self.get_topology_with_links(node_names)

        # Build lookup: name -> list of links
        links_by_node = {}
        for link in link_rows:
            from_dev = link['from_device']
            to_dev = link['to_device']
            topo_link = TopologyLink(
                link_id=link['link_id'],
                from_device=from_dev,
                to_device=to_dev,
                from_ip=link['from_ip'],
                to_ip=link['to_ip'],
                delay=link['delay'],
                data_rate=link['data_rate'],
            )
            links_by_node.setdefault(from_dev, []).append(topo_link)
            # Also add to the 'to' device so both sides have the link
            reverse_link = TopologyLink(
                link_id=link['link_id'],
                from_device=to_dev,
                to_device=from_dev,
                from_ip=link['to_ip'],
                to_ip=link['from_ip'],
                delay=link['delay'],
                data_rate=link['data_rate'],
            )
            links_by_node.setdefault(to_dev, []).append(reverse_link)

        nodes = []
        for row in node_rows:
            name = row['name']
            ns3_ips = row['ns3_ips'] if isinstance(row['ns3_ips'], list) else []
            neighbors = row['neighbors'] if isinstance(row['neighbors'], list) else []
            node_links = links_by_node.get(name, [])

            nodes.append(TopologyNode(
                id=row['node_id'],
                name=name,
                type=row['type'],
                status="Active",  # No status in current schema, defaulting
                ip=row['ip'],
                location=row['location'],
                ns3_ips=ns3_ips,
                neighbors=[str(n) for n in neighbors if n],
                links=node_links,
            ))

        return nodes

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

    def get_neighbours(self, node_id: str) -> list[str]:
        cypher = f"MATCH (n:NetworkNode {{name: \"{node_id}\"}})-[:CONNECTED_TO]-(neighbor:NetworkNode) RETURN neighbor.name AS neighbor_name"
        try:
            raw = self.run_query(cypher)
            rows = self._parse_table(raw)
            return [str(r.get('col0')) for r in rows if r.get('col0')]
        except Exception as e:
            print(f"❌ get_neighbours failed: {e}", file=sys.stderr)
            return []

    def get_blast_radius(self, node_id: str) -> list[str]:
        cypher = f"MATCH (n:NetworkNode {{name: \"{node_id}\"}})-[:CONNECTED_TO*1..5]-(downstream:NetworkNode) WHERE downstream.name <> \"{node_id}\" RETURN DISTINCT downstream.name AS affected_id"
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

        # Collect all links
        all_links = []
        seen_link_ids = set()
        for node in topology:
            for link in node.links:
                if link.link_id not in seen_link_ids:
                    seen_link_ids.add(link.link_id)
                    all_links.append(link)

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

        bundle = ContextBundle(query, topology, telemetry, docs, links=all_links)
        bundle.blast_radius = blast_radius
        print(f"✅ Context built: {len(topology)} nodes, {len(all_links)} links, {len(telemetry)} telemetry, {len(docs)} docs, blast_radius={blast_radius}\n", file=sys.stderr)
        return bundle

    @staticmethod
    def _parse_delay_ms(delay_str: str) -> float:
        """Parse delay string like '1ms', '2ms' into float milliseconds."""
        if not delay_str:
            return 0.0
        try:
            return float(delay_str.lower().replace('ms', '').strip())
        except ValueError:
            return 0.0

    @staticmethod
    def _parse_data_rate_gbps(data_rate_str: str) -> float:
        """Parse data rate string like '5Gbps', '10Gbps' into float Gbps."""
        if not data_rate_str:
            return 0.0
        try:
            return float(data_rate_str.lower().replace('gbps', '').replace('mbps', '').strip())
        except ValueError:
            return 0.0

    def summarize_context(self, bundle: ContextBundle) -> str:
        lines = []
        status_icons = {"active": "✅", "up": "✅", "degraded": "⚠️", "down": "❌", "unknown": "❓"}

        # ============================================================
        # SECTION 1: PRE-COMPUTED FACTS (Model cannot change these)
        # ============================================================
        lines.append("=== SYSTEM-VERIFIED FACTS ===")
        lines.append("(These facts are pre-computed by the system. You MUST use these exact values.)")

        # Device inventory
        type_counts = {}
        for node in bundle.topology:
            dtype = node.type.lower() if node.type else "unknown"
            type_counts[dtype] = type_counts.get(dtype, 0) + 1

        lines.append(f"DEVICE COUNT: {len(bundle.topology)} total devices")
        for dtype, count in sorted(type_counts.items()):
            lines.append(f"  - {count} {dtype}{'s' if count != 1 else ''}")

        # Location summary
        location_counts = {}
        for node in bundle.topology:
            loc = node.location if node.location else "unknown"
            location_counts[loc] = location_counts.get(loc, 0) + 1
        if location_counts:
            loc_summary = ", ".join(f"{count} in {loc}" for loc, count in sorted(location_counts.items()))
            lines.append(f"LOCATIONS: {loc_summary}")

        # Link analysis (pre-computed, NOT offloaded to LLM)
        lines.append(f"LINK COUNT: {len(bundle.links)} direct links")
        if bundle.links:
            delays = [self._parse_delay_ms(l.delay) for l in bundle.links]
            data_rates = [self._parse_data_rate_gbps(l.data_rate) for l in bundle.links]

            min_delay = min(delays)
            max_delay = max(delays)
            avg_delay = sum(delays) / len(delays)
            min_rate = min(data_rates)
            max_rate = max(data_rates)
            avg_rate = sum(data_rates) / len(data_rates)

            lines.append(f"LINK DELAYS: min={min_delay:.0f}ms, max={max_delay:.0f}ms, avg={avg_delay:.1f}ms")
            lines.append(f"LINK DATA RATES: min={min_rate:.0f}Gbps, max={max_rate:.0f}Gbps, avg={avg_rate:.1f}Gbps")

            # Identify bottlenecks (slowest links)
            slowest_links = [l for l in bundle.links if self._parse_delay_ms(l.delay) == max_delay]
            if slowest_links:
                lines.append(f"HIGHEST DELAY LINKS ({max_delay:.0f}ms): " + 
                           ", ".join(f"{l.from_device}↔{l.to_device}" for l in slowest_links))

            # Identify lowest bandwidth links
            lowest_bw_links = [l for l in bundle.links if self._parse_data_rate_gbps(l.data_rate) == min_rate]
            if lowest_bw_links and min_rate < max_rate:
                lines.append(f"LOWEST BANDWIDTH LINKS ({min_rate:.0f}Gbps): " +
                           ", ".join(f"{l.from_device}↔{l.to_device}" for l in lowest_bw_links))

        # Isolated devices
        isolated = [n for n in bundle.topology if not n.neighbors]
        if isolated:
            lines.append(f"ISOLATED DEVICES: {', '.join(n.name for n in isolated)}")

        # Topology type detection (pre-computed)
        if len(bundle.topology) > 1:
            all_nodes = {n.name for n in bundle.topology}
            is_fully_connected = all(
                set(n.neighbors) == all_nodes - {n.name}
                for n in bundle.topology
            )
            if is_fully_connected:
                lines.append("TOPOLOGY TYPE: Full mesh (every device directly connected to every other device)")
            else:
                lines.append("TOPOLOGY TYPE: Partial mesh (not all devices directly connected)")

        if bundle.telemetry:
            lines.append(f"TELEMETRY STATUS: {len(bundle.telemetry)} metrics available across {len(set(t.device_id for t in bundle.telemetry))} devices")
        else:
            lines.append("TELEMETRY STATUS: No telemetry data available")

        if bundle.blast_radius:
            lines.append(f"BLAST RADIUS: {len(bundle.blast_radius)} devices affected - {', '.join(bundle.blast_radius)}")

        lines.append("END OF VERIFIED FACTS")
        lines.append("")

        # ============================================================
        # SECTION 2: DEVICE DETAILS
        # ============================================================
        lines.append("=== DEVICE DETAILS ===")
        if not bundle.topology:
            lines.append("No topology data available.")
        else:
            for node in bundle.topology:
                icon = status_icons.get(node.status, "❓")
                lines.append(f"\n{icon} Device: {node.name}")
                lines.append(f"   Type: {node.type.upper()}  |  Location: {node.location}  |  Management IP: {node.ip if node.ip else 'N/A'}")
                if node.ns3_ips:
                    lines.append(f"   NS3 Interface IPs: {', '.join(node.ns3_ips)}")
                if node.neighbors:
                    lines.append(f"   Neighbors ({len(node.neighbors)}): {', '.join(node.neighbors)}")
                else:
                    lines.append(f"   Neighbors: (none - isolated)")
                # Links for this node
                if node.links:
                    lines.append(f"   Links ({len(node.links)}):")
                    for link in node.links:
                        lines.append(f"      → {link.to_device}: {link.from_ip}→{link.to_ip} | delay: {link.delay} | rate: {link.data_rate}")
                # Telemetry for this node
                device_metrics = [t for t in bundle.telemetry if t.device_id == node.name]
                if device_metrics:
                    metrics_str = []
                    for m in device_metrics:
                        flag = ""
                        if m.metric == "cpu_usage" and m.value > 80:
                            flag = " ⚠️"
                        elif m.metric == "memory_usage" and m.value > 85:
                            flag = " ⚠️"
                        elif m.metric == "packet_loss" and m.value > 1:
                            flag = " ⚠️"
                        metrics_str.append(f"{m.metric}: {m.value}{m.unit}{flag}")
                    lines.append(f"   Metrics: {', '.join(metrics_str)}")

        # ============================================================
        # SECTION 3: KNOWLEDGE BASE DOCS
        # ============================================================
        if bundle.docs:
            lines.append("\n=== RELEVANT KNOWLEDGE ===")
            for doc in bundle.docs[:2]:
                lines.append(f"\n[{doc.category.upper()}] {doc.title}")
                content = doc.content[:300] + "..." if len(doc.content) > 300 else doc.content
                lines.append(f"   {content}")

        # ============================================================
        # FINAL: Truncate if needed
        # ============================================================
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