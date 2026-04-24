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

MODE = os.getenv("CONTEXT_BUILDER_MODE", "live").lower()
print(f"🔧 CONTEXT_BUILDER_MODE = {MODE}", file=sys.stderr)

MAX_CONTEXT_CHARS = 7200
DEVICE_ID_RE = re.compile(r'\b([a-zA-Z]+)[\s-]?(\d+)\b', re.IGNORECASE)
IP_RE = re.compile(r'\b(?:\d{1,3}\.){3}\d{1,3}\b')

# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------
class TopologyLink:
    def __init__(self, link_id, from_device, to_device, from_ip, to_ip, delay, data_rate):
        self.link_id = link_id
        self.from_device = from_device
        self.to_device = to_device
        self.from_ip = from_ip
        self.to_ip = to_ip
        self.delay = delay
        self.data_rate = data_rate

class PathHop:
    """Single hop in a shortest path."""
    def __init__(self, hop_num, from_device, to_device, from_ip, to_ip, delay, data_rate):
        self.hop_num = hop_num
        self.from_device = from_device
        self.to_device = to_device
        self.from_ip = from_ip
        self.to_ip = to_ip
        self.delay = delay
        self.data_rate = data_rate

class ShortestPath:
    """Result of a shortest path query."""
    def __init__(self, from_device, to_device, hops_count, path_devices, hops):
        self.from_device = from_device
        self.to_device = to_device
        self.hops_count = hops_count
        self.path_devices = path_devices  # ordered list of device names
        self.hops = hops or []  # list of PathHop objects

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
        self.links = links or []
        self.cpu = cpu
        self.memory = memory

class TelemetryData:
    def __init__(self, device_id, metric, value, unit, timestamp=None):
        self.device_id = device_id
        self.metric = metric
        self.value = value
        self.unit = unit
        self.timestamp = timestamp or datetime.now()

class ContextBundle:
    def __init__(self, query, topology=None, telemetry=None, links=None, shortest_path=None):
        self.query = query
        self.topology = topology or []
        self.telemetry = telemetry or []
        self.links = links or []
        self.shortest_path = shortest_path
        self.blast_radius = []
        self.timestamp = datetime.now()


# ---------------------------------------------------------------------------
# Neo4j Bridge Client
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

    # ------------------------------------------------------------------
    # CSV / Table Parsing
    # ------------------------------------------------------------------

    @staticmethod
    def _parse_csv_line(line: str) -> list:
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
            elif ch in ('[', '{') and not in_quotes:
                in_brackets += 1
                current += ch
            elif ch in (']', '}') and not in_quotes and in_brackets > 0:
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
    def _parse_neo4j_map_list(raw: str) -> list:
        if not raw or raw.strip() in ("[]", "", "null", "NULL"):
            return []
        raw = raw.strip()
        if raw.startswith("[") and raw.endswith("]"):
            raw = raw[1:-1].strip()
            if not raw:
                return []
        results = []
        depth = 0
        current_map = ""
        for ch in raw:
            if ch == '{':
                depth += 1
                current_map += ch
            elif ch == '}':
                depth -= 1
                current_map += ch
                if depth == 0:
                    parsed = Neo4jBridgeClient._parse_neo4j_map(current_map.strip())
                    if parsed:
                        results.append(parsed)
                    current_map = ""
            elif depth > 0:
                current_map += ch
        return results

    @staticmethod
    def _split_map_pairs(inner: str) -> list:
        pairs = []
        current = ""
        in_quotes = False
        depth = 0
        for ch in inner:
            if ch == '"':
                in_quotes = not in_quotes
                current += ch
            elif ch in ('[', '{') and not in_quotes:
                depth += 1
                current += ch
            elif ch in (']', '}') and not in_quotes:
                depth -= 1
                current += ch
            elif ch == ',' and not in_quotes and depth == 0:
                pairs.append(current.strip())
                current = ""
            else:
                current += ch
        if current.strip():
            pairs.append(current.strip())
        return pairs

    @staticmethod
    def _parse_neo4j_map(map_str: str) -> dict:
        if not map_str or not (map_str.startswith("{") and map_str.endswith("}")):
            return {}
        inner = map_str[1:-1].strip()
        if not inner:
            return {}
        result = {}
        pairs = Neo4jBridgeClient._split_map_pairs(inner)
        for pair in pairs:
            pair = pair.strip()
            if ':' not in pair:
                continue
            colon_idx = pair.index(':')
            key = pair[:colon_idx].strip()
            value_str = pair[colon_idx+1:].strip()
            if value_str.startswith('"') and value_str.endswith('"'):
                value = value_str[1:-1]
            elif value_str.startswith('['):
                value = value_str
            else:
                try:
                    value = int(value_str)
                except ValueError:
                    try:
                        value = float(value_str)
                    except ValueError:
                        value = value_str
            if key:
                result[key] = value
        return result

    @staticmethod
    def _parse_cell(cell: str):
        c = cell.strip()
        if c in ("<null>", "null", "NULL", ""):
            return None
        if c.startswith("[{") and c.endswith("}]"):
            return Neo4jBridgeClient._parse_neo4j_map_list(c)
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

    def _resolve_device_name(self, identifier: str) -> Optional[str]:
        """Resolve an IP or name to a device name. Returns None if not found."""
        # Try exact name match first
        cypher = f"MATCH (n:NetworkNode) WHERE n.name = \"{identifier}\" OR n.ip = \"{identifier}\" OR \"{identifier}\" IN n.ns3_ips RETURN n.name AS name LIMIT 1"
        try:
            raw = self.run_query(cypher)
            rows = self._parse_table(raw)
            if rows:
                return str(rows[0].get('name') or rows[0].get('col0') or '')
        except Exception:
            pass
        return None

    def get_shortest_path(self, from_id: str, to_id: str) -> Optional[ShortestPath]:
        """Find shortest path (by hop count, like RIP) between two devices."""
        from_name = self._resolve_device_name(from_id)
        to_name = self._resolve_device_name(to_id)

        if not from_name:
            print(f"❌ Could not resolve source: {from_id}", file=sys.stderr)
            return None
        if not to_name:
            print(f"❌ Could not resolve target: {to_id}", file=sys.stderr)
            return None

        cypher = (
            f"MATCH (start:NetworkNode {{name: \"{from_name}\"}}), "
            f"(end:NetworkNode {{name: \"{to_name}\"}}), "
            f"path = shortestPath((start)-[:CONNECTED_TO*]-(end)) "
            f"RETURN nodes(path) AS node_list, relationships(path) AS rel_list, length(path) AS hops"
        )

        try:
            raw = self.run_query(cypher)
            rows = self._parse_table(raw)
        except Exception as e:
            print(f"❌ get_shortest_path failed: {e}", file=sys.stderr)
            return None

        if not rows:
            print(f"⚠️ No path found between {from_name} and {to_name}", file=sys.stderr)
            return None

        row = rows[0]
        hops_count = row.get('hops') or row.get('col2') or 0
        hops_count = int(hops_count) if hops_count is not None else 0

        # Parse node list - handled by _parse_cell which may return parsed node objects
        node_list_raw = row.get('node_list') or row.get('col0') or []
        rel_list_raw = row.get('rel_list') or row.get('col1') or []

        # Build ordered device name list
        path_devices = []
        if isinstance(node_list_raw, list):
            for node_item in node_list_raw:
                if isinstance(node_item, dict):
                    path_devices.append(str(node_item.get('name', '')))
                elif isinstance(node_item, str):
                    # Try to extract name from string representation
                    name_match = re.search(r'name:\s*"([^"]+)"', node_item)
                    if name_match:
                        path_devices.append(name_match.group(1))
                    else:
                        path_devices.append(node_item)

        # Build hop details
        path_hops = []
        if isinstance(rel_list_raw, list) and len(rel_list_raw) > 0:
            # rel_list_raw might be a list containing a single list of relationships
            flat_rels = rel_list_raw
            if len(rel_list_raw) == 1 and isinstance(rel_list_raw[0], list):
                flat_rels = rel_list_raw[0]

            for i, rel_item in enumerate(flat_rels):
                if isinstance(rel_item, dict):
                    from_ip = str(rel_item.get('from_interface_ip', ''))
                    to_ip = str(rel_item.get('to_interface_ip', ''))
                    delay = str(rel_item.get('delay', 'unknown'))
                    data_rate = str(rel_item.get('data_rate', 'unknown'))
                    from_dev = path_devices[i] if i < len(path_devices) else '?'
                    to_dev = path_devices[i+1] if i+1 < len(path_devices) else '?'
                    path_hops.append(PathHop(
                        hop_num=i+1,
                        from_device=from_dev,
                        to_device=to_dev,
                        from_ip=from_ip,
                        to_ip=to_ip,
                        delay=delay,
                        data_rate=data_rate,
                    ))
                elif isinstance(rel_item, str):
                    # Parse from string representation
                    from_ip_match = re.search(r'from_interface_ip:\s*"([^"]+)"', rel_item)
                    to_ip_match = re.search(r'to_interface_ip:\s*"([^"]+)"', rel_item)
                    delay_match = re.search(r'delay:\s*"([^"]+)"', rel_item)
                    rate_match = re.search(r'data_rate:\s*"([^"]+)"', rel_item)
                    from_dev = path_devices[i] if i < len(path_devices) else '?'
                    to_dev = path_devices[i+1] if i+1 < len(path_devices) else '?'
                    path_hops.append(PathHop(
                        hop_num=i+1,
                        from_device=from_dev,
                        to_device=to_dev,
                        from_ip=from_ip_match.group(1) if from_ip_match else '?',
                        to_ip=to_ip_match.group(1) if to_ip_match else '?',
                        delay=delay_match.group(1) if delay_match else 'unknown',
                        data_rate=rate_match.group(1) if rate_match else 'unknown',
                    ))

        return ShortestPath(
            from_device=from_name,
            to_device=to_name,
            hops_count=hops_count,
            path_devices=path_devices,
            hops=path_hops,
        )

    def get_topology_with_links(self, node_names: Optional[list] = None) -> tuple:
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
            node_name = str(row.get('node_name') or row.get('col0') or '')
            ns3_ips_raw = row.get('ns3_ips') or row.get('col4') or []
            ns3_ips = ns3_ips_raw if isinstance(ns3_ips_raw, list) else []
            neighbors_raw = row.get('neighbors') or row.get('col6') or []
            neighbors = neighbors_raw if isinstance(neighbors_raw, list) else []

            node_rows.append({
                'name': node_name,
                'type': str(row.get('type') or row.get('col1') or 'unknown'),
                'ip': str(row.get('ip') or row.get('col2') or ''),
                'location': str(row.get('location') or row.get('col3') or ''),
                'ns3_ips': ns3_ips,
                'node_id': str(row.get('node_id') or row.get('col5') or ''),
                'neighbors': neighbors,
            })

            link_data_list = row.get('link_data') or row.get('col7') or []
            if isinstance(link_data_list, list):
                for link in link_data_list:
                    if link is None or not isinstance(link, dict):
                        continue
                    other = link.get('other_name', '')
                    link_id = link.get('id')
                    from_ip = link.get('from_ip', '')
                    to_ip = link.get('to_ip', '')
                    delay = link.get('delay', '')
                    data_rate = link.get('data_rate', '')
                    if other and node_name and link_id is not None:
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
        node_names = node_ids if node_ids else None
        node_rows, link_rows = self.get_topology_with_links(node_names)
        links_by_node = {}
        for link in link_rows:
            from_dev = link['from_device']
            to_dev = link['to_device']
            topo_link = TopologyLink(
                link_id=link['link_id'], from_device=from_dev, to_device=to_dev,
                from_ip=link['from_ip'], to_ip=link['to_ip'],
                delay=link['delay'], data_rate=link['data_rate'],
            )
            links_by_node.setdefault(from_dev, []).append(topo_link)
            links_by_node.setdefault(to_dev, []).append(TopologyLink(
                link_id=link['link_id'], from_device=to_dev, to_device=from_dev,
                from_ip=link['to_ip'], to_ip=link['from_ip'],
                delay=link['delay'], data_rate=link['data_rate'],
            ))
        nodes = []
        for row in node_rows:
            name = row['name']
            ns3_ips = row['ns3_ips'] if isinstance(row['ns3_ips'], list) else []
            neighbors = row['neighbors'] if isinstance(row['neighbors'], list) else []
            nodes.append(TopologyNode(
                id=row['node_id'], name=name, type=row['type'], status="Active",
                ip=row['ip'], location=row['location'], ns3_ips=ns3_ips,
                neighbors=[str(n) for n in neighbors if n],
                links=links_by_node.get(name, []),
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
            metrics_map = [
                (row.get('col1'), "cpu_usage", "%"),
                (row.get('col2'), "memory_usage", "%"),
                (row.get('col3'), "packet_loss", "%"),
                (row.get('col4'), "bandwidth_mbps", "Mbps"),
                (row.get('col5'), "latency_ms", "ms"),
            ]
            for value, metric_name, unit in metrics_map:
                if value is not None and str(value).lower() not in ("null", "none", ""):
                    try:
                        results.append(TelemetryData(
                            device_id=device_id, metric=metric_name,
                            value=round(float(value), 2), unit=unit, timestamp=now
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
# Context Builder
# ---------------------------------------------------------------------------
class ContextBuilder:
    def __init__(self):
        self._mode = MODE
        self._neo4j = None
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

    def get_topology(self, device_ids: Optional[list] = None) -> list[TopologyNode]:
        if not self._neo4j:
            return []
        return self._neo4j.get_topology(device_ids)

    def get_telemetry(self, device_names: Optional[list] = None) -> list[TelemetryData]:
        if not self._neo4j:
            return []
        return self._neo4j.get_telemetry(device_names)

    def get_blast_radius(self, device_id: str) -> list[str]:
        if self._neo4j:
            return self._neo4j.get_blast_radius(device_id)
        return []

    def get_shortest_path(self, from_id: str, to_id: str) -> Optional[ShortestPath]:
        if self._neo4j:
            return self._neo4j.get_shortest_path(from_id, to_id)
        return None

    def _extract_path_endpoints(self, query: str) -> tuple[Optional[str], Optional[str]]:
        """Extract 'from X to Y' pattern from query. Returns (from_id, to_id)."""
        path_patterns = [
            re.compile(r'(?:shortest\s+)?path\s+(?:from\s+)?(\S+)\s+to\s+(\S+)', re.IGNORECASE),
            re.compile(r'between\s+(\S+)\s+and\s+(\S+)', re.IGNORECASE),
            re.compile(r'from\s+(\S+)\s+to\s+(\S+)', re.IGNORECASE),
        ]
        for pattern in path_patterns:
            match = pattern.search(query)
            if match:
                return match.group(1).strip('"\','), match.group(2).strip('"\',')
        # Fallback: look for IPs or device names mentioned
        ips = IP_RE.findall(query)
        if len(ips) >= 2:
            return ips[0], ips[1]
        devices = DEVICE_ID_RE.findall(query.lower())
        if len(devices) >= 2:
            return f"{devices[0][0]}{devices[0][1]}", f"{devices[1][0]}{devices[1][1]}"
        return None, None

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

        blast_radius = []
        for node in topology:
            if node.name and node.name.lower() in query.lower():
                blast_radius.extend(self.get_blast_radius(node.id))
        blast_radius = list(set(blast_radius))

        # Shortest path detection
        shortest_path = None
        if any(kw in query.lower() for kw in ['shortest path', 'path from', 'path between', 'route from']):
            from_id, to_id = self._extract_path_endpoints(query)
            if from_id and to_id:
                print(f"🛤️ Computing shortest path: {from_id} → {to_id}", file=sys.stderr)
                shortest_path = self.get_shortest_path(from_id, to_id)
                if shortest_path:
                    print(f"✅ Path found: {shortest_path.hops_count} hops, {' → '.join(shortest_path.path_devices)}", file=sys.stderr)

        bundle = ContextBundle(query, topology, telemetry, links=all_links, shortest_path=shortest_path)
        bundle.blast_radius = blast_radius
        print(f"✅ Context built: {len(topology)} nodes, {len(all_links)} links, {len(telemetry)} telemetry, blast_radius={blast_radius}, path={'yes' if shortest_path else 'no'}\n", file=sys.stderr)
        return bundle

    @staticmethod
    def _parse_delay_ms(delay_str: str) -> float:
        if not delay_str:
            return 0.0
        try:
            return float(delay_str.lower().replace('ms', '').strip())
        except ValueError:
            return 0.0

    @staticmethod
    def _parse_data_rate_gbps(data_rate_str: str) -> float:
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
        # SECTION 1: SYSTEM-VERIFIED FACTS
        # ============================================================
        lines.append("=== SYSTEM-VERIFIED FACTS ===")
        lines.append("(These facts are pre-computed by the system. You MUST use these exact values.)")

        type_counts = {}
        for node in bundle.topology:
            dtype = node.type.lower() if node.type else "unknown"
            type_counts[dtype] = type_counts.get(dtype, 0) + 1

        lines.append(f"DEVICE COUNT: {len(bundle.topology)} total devices")
        for dtype, count in sorted(type_counts.items()):
            lines.append(f"  - {count} {dtype}{'s' if count != 1 else ''}")

        location_counts = {}
        for node in bundle.topology:
            loc = node.location if node.location else "unknown"
            location_counts[loc] = location_counts.get(loc, 0) + 1
        if location_counts:
            loc_summary = ", ".join(f"{count} in {loc}" for loc, count in sorted(location_counts.items()))
            lines.append(f"LOCATIONS: {loc_summary}")

        lines.append(f"LINK COUNT: {len(bundle.links)} direct links")
        if bundle.links:
            delays = [self._parse_delay_ms(l.delay) for l in bundle.links]
            data_rates = [self._parse_data_rate_gbps(l.data_rate) for l in bundle.links]
            min_delay, max_delay = min(delays), max(delays)
            avg_delay = sum(delays) / len(delays)
            min_rate, max_rate = min(data_rates), max(data_rates)
            avg_rate = sum(data_rates) / len(data_rates)
            lines.append(f"LINK DELAYS: min={min_delay:.0f}ms, max={max_delay:.0f}ms, avg={avg_delay:.1f}ms")
            lines.append(f"LINK DATA RATES: min={min_rate:.0f}Gbps, max={max_rate:.0f}Gbps, avg={avg_rate:.1f}Gbps")
            slowest = [l for l in bundle.links if self._parse_delay_ms(l.delay) == max_delay]
            if slowest:
                lines.append(f"HIGHEST DELAY LINKS ({max_delay:.0f}ms): " + ", ".join(f"{l.from_device}↔{l.to_device}" for l in slowest))
            lowest_bw = [l for l in bundle.links if self._parse_data_rate_gbps(l.data_rate) == min_rate]
            if lowest_bw and min_rate < max_rate:
                lines.append(f"LOWEST BANDWIDTH LINKS ({min_rate:.0f}Gbps): " + ", ".join(f"{l.from_device}↔{l.to_device}" for l in lowest_bw))
            highest_bw = [l for l in bundle.links if self._parse_data_rate_gbps(l.data_rate) == max_rate]
            if highest_bw:
                lines.append(f"HIGHEST BANDWIDTH LINKS ({max_rate:.0f}Gbps): " + ", ".join(f"{l.from_device}↔{l.to_device}" for l in highest_bw))

        isolated = [n for n in bundle.topology if not n.neighbors]
        if isolated:
            lines.append(f"ISOLATED DEVICES: {', '.join(n.name for n in isolated)}")

        if len(bundle.topology) > 1:
            all_nodes = {n.name for n in bundle.topology}
            if all(set(n.neighbors) == all_nodes - {n.name} for n in bundle.topology):
                lines.append("TOPOLOGY TYPE: Full mesh")
            else:
                lines.append("TOPOLOGY TYPE: Partial mesh")

        if bundle.telemetry:
            lines.append(f"TELEMETRY STATUS: {len(bundle.telemetry)} metrics across {len(set(t.device_id for t in bundle.telemetry))} devices")
        else:
            lines.append("TELEMETRY STATUS: No telemetry data available")

        if bundle.blast_radius:
            lines.append(f"BLAST RADIUS: {len(bundle.blast_radius)} devices - {', '.join(bundle.blast_radius)}")

        # ============================================================
        # SHORTEST PATH SECTION
        # ============================================================
        if bundle.shortest_path:
            sp = bundle.shortest_path
            lines.append("")
            lines.append(f"=== SHORTEST PATH: {sp.from_device} → {sp.to_device} ===")
            lines.append(f"HOPS: {sp.hops_count}")
            lines.append(f"DEVICES IN ORDER: {' → '.join(sp.path_devices)}")
            if sp.hops:
                total_delay = sum(self._parse_delay_ms(h.delay) for h in sp.hops)
                rates = [self._parse_data_rate_gbps(h.data_rate) for h in sp.hops]
                bottleneck = min(rates) if rates else 0
                lines.append(f"TOTAL DELAY: {total_delay:.0f}ms")
                lines.append(f"BOTTLENECK BANDWIDTH: {bottleneck:.0f}Gbps")
                lines.append("HOP-BY-HOP DETAILS:")
                for hop in sp.hops:
                    lines.append(f"  Hop {hop.hop_num}: {hop.from_device} → {hop.to_device} | {hop.from_ip}→{hop.to_ip} | delay: {hop.delay} | rate: {hop.data_rate}")

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
                if node.links:
                    lines.append(f"   Links ({len(node.links)}):")
                    for link in node.links:
                        lines.append(f"      → {link.to_device}: {link.from_ip}→{link.to_ip} | delay: {link.delay} | rate: {link.data_rate}")
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