"""
generate_data.py
================
Populates InfluxDB, Neo4j, and ChromaDB with realistic synthetic
network data so the LLM layer runs against real databases
instead of hardcoded stubs.

Run once before starting the LLM service:
    python generate_data.py

Run with --reset to wipe and repopulate:
    python generate_data.py --reset

Dependencies:
    pip install influxdb-client neo4j chromadb sentence-transformers

Services expected:
    InfluxDB:  http://localhost:8086  (token in .env or INFLUX_TOKEN)
    Neo4j:     bolt://localhost:7687  (user: neo4j, pass: password)
    ChromaDB:  embedded, ./chroma_store/
"""

import os
import sys
import math
import random
import argparse
import time as _time
from datetime import datetime, timedelta, timezone

# ── InfluxDB
from influxdb_client import InfluxDBClient, Point, WritePrecision
from influxdb_client.client.write_api import SYNCHRONOUS

# ── Neo4j
from neo4j import GraphDatabase

# ── ChromaDB + embeddings
import chromadb
from chromadb.utils import embedding_functions

# ---------------------------------------------------------------------------
# Configuration — override via env vars or edit here
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

# How many minutes of historical metrics to generate
HISTORY_MINUTES = 60

# ---------------------------------------------------------------------------
# Topology definition — single source of truth
# All three databases are populated from this same structure.
# ---------------------------------------------------------------------------
DEVICES = [
    {"id": "router-1",  "name": "Core Router",          "type": "router",   "ip": "10.0.0.1",  "location": "core",  "status": "up"},
    {"id": "switch-1",  "name": "Access Switch 1",       "type": "switch",   "ip": "10.0.1.1",  "location": "core",  "status": "up"},
    {"id": "switch-2",  "name": "Distribution Switch",   "type": "switch",   "ip": "10.0.1.2",  "location": "core",  "status": "degraded"},
    {"id": "server-1",  "name": "Web Server",            "type": "server",   "ip": "10.0.2.1",  "location": "dmz",   "status": "up"},
    {"id": "server-2",  "name": "Database Server",       "type": "server",   "ip": "10.0.2.2",  "location": "dmz",   "status": "up"},
    {"id": "server-3",  "name": "Application Server",    "type": "server",   "ip": "10.0.2.3",  "location": "dmz",   "status": "down"},
    {"id": "fw-1",      "name": "Edge Firewall",         "type": "firewall", "ip": "10.0.0.254","location": "edge",  "status": "up"},
]

LINKS = [
    {"src": "router-1", "dst": "switch-1",  "interface_src": "Gi0/0", "interface_dst": "Gi0/1", "bandwidth_mbps": 1000, "latency_ms": 0.5},
    {"src": "router-1", "dst": "switch-2",  "interface_src": "Gi0/1", "interface_dst": "Gi0/1", "bandwidth_mbps": 1000, "latency_ms": 0.5},
    {"src": "router-1", "dst": "fw-1",      "interface_src": "Gi0/2", "interface_dst": "Gi0/0", "bandwidth_mbps": 1000, "latency_ms": 0.3},
    {"src": "switch-1", "dst": "server-1",  "interface_src": "Gi0/2", "interface_dst": "eth0",  "bandwidth_mbps": 100,  "latency_ms": 0.2},
    {"src": "switch-1", "dst": "server-2",  "interface_src": "Gi0/3", "interface_dst": "eth0",  "bandwidth_mbps": 100,  "latency_ms": 0.2},
    {"src": "switch-2", "dst": "server-3",  "interface_src": "Gi0/2", "interface_dst": "eth0",  "bandwidth_mbps": 100,  "latency_ms": 0.2},
]

SUBNETS = [
    {"cidr": "10.0.0.0/24", "name": "Core Network",  "vlan_id": 10, "devices": ["router-1", "fw-1"]},
    {"cidr": "10.0.1.0/24", "name": "Switch Network", "vlan_id": 20, "devices": ["switch-1", "switch-2"]},
    {"cidr": "10.0.2.0/24", "name": "Server Network", "vlan_id": 30, "devices": ["server-1", "server-2", "server-3"]},
]

# ---------------------------------------------------------------------------
# Metric profiles — realistic per-device baselines + anomaly injection
# ---------------------------------------------------------------------------
# Each device has baseline ranges. Anomaly devices get injected spikes.
METRIC_PROFILES = {
    "router-1":  {"cpu": (30, 50),  "memory": (40, 60),  "latency": (10, 15), "packet_loss": (0.0, 0.1), "bandwidth": (300, 400)},
    "switch-1":  {"cpu": (10, 20),  "memory": (20, 35),  "latency": (2, 5),   "packet_loss": (0.0, 0.05),"bandwidth": (80, 120)},
    "switch-2":  {"cpu": (40, 60),  "memory": (50, 70),  "latency": (4, 8),   "packet_loss": (1.5, 3.0), "bandwidth": (80, 120)},  # degraded
    "server-1":  {"cpu": (20, 45),  "memory": (50, 65),  "latency": (2, 4),   "packet_loss": (0.0, 0.05),"bandwidth": (40, 80)},
    "server-2":  {"cpu": (25, 55),  "memory": (55, 75),  "latency": (2, 4),   "packet_loss": (0.0, 0.05),"bandwidth": (20, 60)},
    "server-3":  {"cpu": (85, 99),  "memory": (88, 97),  "latency": (0, 0),   "packet_loss": (0.0, 0.0), "bandwidth": (0, 0)},    # down — zeroes for net
    "fw-1":      {"cpu": (15, 30),  "memory": (30, 45),  "latency": (1, 3),   "packet_loss": (0.0, 0.02),"bandwidth": (200, 350)},
}

def _sample(lo: float, hi: float, noise: float = 0.05) -> float:
    """Sample a value in [lo, hi] with small Gaussian noise."""
    base = random.uniform(lo, hi)
    base += random.gauss(0, (hi - lo) * noise)
    return round(max(lo * 0.9, min(hi * 1.1, base)), 2)


# ---------------------------------------------------------------------------
# InfluxDB population
# ---------------------------------------------------------------------------

def populate_influxdb(reset: bool = False):
    print("\n── InfluxDB ──────────────────────────────────────")

    client = InfluxDBClient(url=INFLUX_URL, token=INFLUX_TOKEN, org=INFLUX_ORG)
    buckets_api = client.buckets_api()
    write_api   = client.write_api(write_options=SYNCHRONOUS)

    # Create bucket (or confirm it exists)
    existing = [b.name for b in buckets_api.find_buckets().buckets]
    if INFLUX_BUCKET in existing:
        if reset:
            bucket = buckets_api.find_bucket_by_name(INFLUX_BUCKET)
            buckets_api.delete_bucket(bucket)
            print(f"  Deleted existing bucket '{INFLUX_BUCKET}'")
        else:
            print(f"  Bucket '{INFLUX_BUCKET}' already exists — appending data")
    
    if INFLUX_BUCKET not in existing or reset:
        buckets_api.create_bucket(bucket_name=INFLUX_BUCKET, org=INFLUX_ORG)
        print(f"  Created bucket '{INFLUX_BUCKET}'")

    # Write HISTORY_MINUTES of historical data, one point per device per minute
    now = datetime.now(timezone.utc)
    points = []

    for minute in range(HISTORY_MINUTES, 0, -1):
        ts = now - timedelta(minutes=minute)

        for device in DEVICES:
            did   = device["id"]
            dtype = device["type"]
            prof  = METRIC_PROFILES[did]

            # server-3 went down ~20 minutes ago: zero out network metrics
            is_server3_down = (did == "server-3" and minute <= 20)

            p = (
                Point("device_metrics")
                .tag("device_id",   did)
                .tag("device_type", dtype)
                .tag("location",    device["location"])
                .tag("status",      device["status"])
                .field("cpu_usage",      _sample(*prof["cpu"]))
                .field("memory_usage",   _sample(*prof["memory"]))
                .field("latency_ms",     0.0 if is_server3_down else _sample(*prof["latency"]))
                .field("packet_loss",    0.0 if is_server3_down else _sample(*prof["packet_loss"]))
                .field("bandwidth_mbps", 0.0 if is_server3_down else _sample(*prof["bandwidth"]))
                .field("error_rate",     round(random.uniform(0, 0.5 if did == "switch-2" else 0.01), 4))
                .time(ts, WritePrecision.S)
            )
            points.append(p)

    write_api.write(bucket=INFLUX_BUCKET, org=INFLUX_ORG, record=points)
    print(f"  Written {len(points)} data points ({HISTORY_MINUTES} min × {len(DEVICES)} devices)")

    # Write a current snapshot (t=now) for each device
    current_points = []
    for device in DEVICES:
        did  = device["id"]
        prof = METRIC_PROFILES[did]
        p = (
            Point("device_metrics")
            .tag("device_id",   did)
            .tag("device_type", device["type"])
            .tag("location",    device["location"])
            .tag("status",      device["status"])
            .field("cpu_usage",      _sample(*prof["cpu"]))
            .field("memory_usage",   _sample(*prof["memory"]))
            .field("latency_ms",     0.0 if did == "server-3" else _sample(*prof["latency"]))
            .field("packet_loss",    0.0 if did == "server-3" else _sample(*prof["packet_loss"]))
            .field("bandwidth_mbps", 0.0 if did == "server-3" else _sample(*prof["bandwidth"]))
            .field("error_rate",     round(random.uniform(0, 0.05), 4))
            .time(now, WritePrecision.S)
        )
        current_points.append(p)

    write_api.write(bucket=INFLUX_BUCKET, org=INFLUX_ORG, record=current_points)
    print(f"  Written {len(current_points)} current snapshots")
    client.close()
    print("  ✅ InfluxDB done")


# ---------------------------------------------------------------------------
# Neo4j population
# ---------------------------------------------------------------------------

def populate_neo4j(reset: bool = False):
    print("\n── Neo4j ─────────────────────────────────────────")

    driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))

    with driver.session() as session:
        if reset:
            session.run("MATCH (n) DETACH DELETE n")
            print("  Cleared existing graph")

        # Constraints (idempotent)
        session.run("CREATE CONSTRAINT IF NOT EXISTS FOR (d:Device) REQUIRE d.id IS UNIQUE")
        session.run("CREATE CONSTRAINT IF NOT EXISTS FOR (s:Subnet) REQUIRE s.cidr IS UNIQUE")

        # Create Device nodes
        for device in DEVICES:
            session.run("""
                MERGE (d:Device {id: $id})
                SET d.name     = $name,
                    d.type     = $type,
                    d.ip       = $ip,
                    d.location = $location,
                    d.status   = $status
            """, **device)
        print(f"  Created {len(DEVICES)} Device nodes")

        # Create CONNECTED_TO relationships (bidirectional)
        for link in LINKS:
            session.run("""
                MATCH (a:Device {id: $src}), (b:Device {id: $dst})
                MERGE (a)-[r:CONNECTED_TO {interface_src: $interface_src}]->(b)
                SET r.interface_dst  = $interface_dst,
                    r.bandwidth_mbps = $bandwidth_mbps,
                    r.latency_ms     = $latency_ms
                MERGE (b)-[r2:CONNECTED_TO {interface_src: $interface_dst}]->(a)
                SET r2.interface_dst  = $interface_src,
                    r2.bandwidth_mbps = $bandwidth_mbps,
                    r2.latency_ms     = $latency_ms
            """, **link)
        print(f"  Created {len(LINKS) * 2} CONNECTED_TO relationships (bidirectional)")

        # Create Subnet nodes and PART_OF relationships
        for subnet in SUBNETS:
            session.run("""
                MERGE (s:Subnet {cidr: $cidr})
                SET s.name    = $name,
                    s.vlan_id = $vlan_id
            """, cidr=subnet["cidr"], name=subnet["name"], vlan_id=subnet["vlan_id"])
            for dev_id in subnet["devices"]:
                session.run("""
                    MATCH (d:Device {id: $dev_id}), (s:Subnet {cidr: $cidr})
                    MERGE (d)-[:PART_OF]->(s)
                """, dev_id=dev_id, cidr=subnet["cidr"])
        print(f"  Created {len(SUBNETS)} Subnet nodes with PART_OF relationships")

        # Verify
        result = session.run("MATCH (d:Device) RETURN count(d) as c")
        count  = result.single()["c"]
        print(f"  Verified: {count} Device nodes in graph")

    driver.close()
    print("  ✅ Neo4j done")


# ---------------------------------------------------------------------------
# ChromaDB population
# ---------------------------------------------------------------------------

DOCUMENTS = [
    {
        "id": "runbook-server-down",
        "title": "Runbook: Server Unreachable",
        "category": "runbook",
        "content": """
Standard procedure when a server is unreachable or shows 'down' status:

1. VERIFY NETWORK PATH: Check the upstream switch status first. Use Neo4j to trace
   the path from the affected server to the core router. A degraded upstream switch
   is the most common cause of server unreachability.

2. CHECK LAST TELEMETRY: Query InfluxDB for the last known metrics before the server
   went offline. High CPU (>90%) or memory (>95%) just before the outage suggests
   resource exhaustion rather than a network failure.

3. PING TEST: Attempt ICMP ping from an adjacent device on the same subnet.
   If ping succeeds but application is down, it's a software issue, not network.

4. CHECK INTERFACE COUNTERS: On the upstream switch, verify the interface connected
   to the server shows no CRC errors, input errors, or output drops.

5. ESCALATION: If steps 1-4 don't identify the cause, escalate to on-site team
   for physical inspection. Do not attempt remote restart until network path is verified.

Common causes: upstream switch degradation, resource exhaustion, NIC failure,
OS kernel panic, storage I/O hang.
        """.strip()
    },
    {
        "id": "runbook-switch-degraded",
        "title": "Runbook: Switch Degraded Status",
        "category": "runbook",
        "content": """
Procedure for investigating a switch in 'degraded' state:

1. SPANNING TREE CHECK: Switch degradation most commonly indicates a Spanning Tree
   Protocol (STP) issue. Check for topology change notifications (TCN) and verify
   root bridge assignment has not changed unexpectedly.

2. INTERFACE ERRORS: Review all interfaces for elevated error counters:
   - CRC errors: indicate physical layer issues (cable, SFP)
   - Input errors: buffer overflow, noise
   - Output drops: congestion, queue exhaustion
   - Runts/Giants: misconfigured MTU

3. CPU AND MEMORY: A switch at >60% CPU is abnormal. Common causes:
   - Broadcast storm
   - MAC address table thrashing
   - Spanning tree reconvergence loop

4. PACKET LOSS THRESHOLD: Packet loss >1% on any switch interface is a critical alert.
   Above 2% indicates likely hardware fault or severe congestion.

5. DOWNSTREAM IMPACT: Query the topology graph to identify all devices downstream of
   this switch. These devices are at risk — notify relevant teams before taking action.

6. REMEDIATION: If STP reconvergence is ongoing, do NOT restart the switch — this
   will cause a full reconvergence event and worsen the outage. Instead, identify
   the port causing instability and shut it down first.
        """.strip()
    },
    {
        "id": "runbook-high-cpu-router",
        "title": "Runbook: High CPU on Router",
        "category": "runbook",
        "content": """
Procedure for investigating high CPU utilization on a core router (>70%):

1. IDENTIFY TOP PROCESS: Connect to the router management interface and run
   'show processes cpu sorted'. Identify the process consuming the most CPU.

2. ROUTING PROTOCOL CHURN: If the BGP or OSPF process is spiking, check for:
   - Neighbor flapping (repeated adjacency resets)
   - Route table thrashing (large number of route adds/withdraws)
   - BGP update storms from a peer

3. ACL PROCESSING: Software-processed ACLs are CPU-intensive. Verify ACLs are
   hardware-accelerated where possible. Remove overly broad 'permit any' rules.

4. INTERFACE ERRORS: High input error rates cause the CPU to process error recovery.
   Check all interfaces for elevated error counters.

5. TRAFFIC SPIKE: A legitimate traffic surge (DDoS, backup job, large file transfer)
   can spike router CPU. Verify bandwidth utilization on uplink interfaces.

6. DO NOT RESTART: Restarting a core router will cause a full network outage for all
   downstream devices. Only restart as a last resort after all other options are
   exhausted and a maintenance window is scheduled.

Normal CPU range: 20-50%. Alert threshold: 70%. Critical threshold: 85%.
        """.strip()
    },
    {
        "id": "policy-change-management",
        "title": "Network Change Management Policy",
        "category": "policy",
        "content": """
All configuration changes to network devices must follow this change management procedure:

CHANGE CATEGORIES:
- Emergency: Immediate action required to restore service. Requires post-change review.
- Standard: Pre-approved recurring changes (e.g., VLAN additions). No approval needed.
- Normal: All other changes. Requires change ticket and approval before execution.

APPROVAL REQUIREMENTS:
- Core router changes: Requires network lead approval + change window
- Firewall rule changes: Requires security team approval
- Switch VLAN/trunk changes: Requires network lead approval
- Server network interface changes: Requires server team notification

ROLLBACK REQUIREMENT: All normal and emergency changes must have a documented
rollback procedure before execution. Configuration backup must be taken immediately
before any change is applied.

CHANGE WINDOW: Core infrastructure changes should be scheduled during the maintenance
window (Sunday 02:00-06:00 local time) unless classified as emergency.

AUTOMATED ACTIONS: Any automated action from the LLM/AI system that modifies device
configuration is classified as a Normal change and requires human approval before
execution, regardless of the AI system's confidence score.
        """.strip()
    },
    {
        "id": "config-router-1",
        "title": "router-1 Configuration Snapshot",
        "category": "config",
        "content": """
Device: router-1 (Core Router)
IP: 10.0.0.1
Last config backup: automated

Interfaces:
  Gi0/0 — connected to switch-1 (10.0.1.1), 1Gbps, status: up
  Gi0/1 — connected to switch-2 (10.0.1.2), 1Gbps, status: up
  Gi0/2 — connected to fw-1 (10.0.0.254), 1Gbps, status: up

Routing:
  Protocol: OSPF (area 0)
  Redistributed: connected, static
  Default route: via fw-1 (10.0.0.254)

ACLs:
  ACL-MGMT: permit tcp 10.0.0.0/24 any eq 22  (management SSH)
  ACL-DENY-EXTERNAL: deny ip any 10.0.0.0/8   (block external RFC1918)

Note: router-1 is the single core router. Any restart causes full network outage.
Restart requires change ticket, network lead approval, and maintenance window.
        """.strip()
    },
    {
        "id": "config-switch-2",
        "title": "switch-2 Configuration Snapshot",
        "category": "config",
        "content": """
Device: switch-2 (Distribution Switch)
IP: 10.0.1.2
Current status: DEGRADED
Last config backup: automated

Interfaces:
  Gi0/1 — uplink to router-1 (10.0.0.1), 1Gbps, status: up
  Gi0/2 — connected to server-3 (10.0.2.3), 100Mbps, status: up (device down)

VLANs: 10 (Core), 30 (Server Network)
Spanning Tree: RSTP, this switch is non-root

Current Alerts:
  - Packet loss on Gi0/1: 2.3% (threshold: 1.0%) — ACTIVE since 12 min ago
  - CPU utilization: 52% (threshold: 60%) — WARNING

Note: switch-2 is the sole upstream path for server-3. Degraded state on this
switch is the likely cause of server-3 unreachability.
        """.strip()
    },
    {
        "id": "incident-2024-001",
        "title": "Past Incident: Switch-2 Packet Loss (Similar Pattern)",
        "category": "incident",
        "content": """
Incident ID: INC-2024-001
Date: 2024-11-14
Severity: High
Duration: 47 minutes
Affected devices: switch-2, server-3

SUMMARY: switch-2 experienced 2.1% packet loss on its uplink interface, causing
server-3 to become intermittently unreachable. The root cause was identified as
a failing SFP transceiver on Gi0/1.

TIMELINE:
  14:22 — First alert: packet_loss on switch-2 Gi0/1 exceeded 1% threshold
  14:31 — server-3 reported down by monitoring system
  14:45 — On-site engineer identified CRC errors on Gi0/1
  14:58 — SFP transceiver replaced on Gi0/1
  15:09 — Packet loss returned to 0%, server-3 restored

ROOT CAUSE: Degraded SFP transceiver causing intermittent bit errors at the
physical layer, manifesting as packet loss and eventual link instability.

RESOLUTION: Replace SFP transceiver on switch-2 Gi0/1.

LESSONS LEARNED: When switch-2 shows packet loss >1%, immediately check SFP
health before investigating software/config causes. Physical layer issues are
the most common cause of this specific symptom on this device.
        """.strip()
    },
    {
        "id": "doc-digital-twin-overview",
        "title": "Network Digital Twin: System Overview",
        "category": "documentation",
        "content": """
A network digital twin is a real-time virtual representation of the physical network
that enables monitoring, simulation, prediction, and autonomous control.

COMPONENTS:
- Physical Layer: Routers and switches emitting telemetry via gNMI, config via NetConf
- Network Information Layer: Time-series DB (metrics), Graph DB (topology), Vector DB (knowledge)
- Control Layer: Policy enforcement, action planning, change execution
- LLM Layer: Natural language interface, intent parsing, automated reasoning

DATA FLOWS:
- Telemetry: device → gNMI collector → InfluxDB → LLM context
- Topology: device → NetConf → Neo4j → LLM context
- Knowledge: runbooks/configs → embeddings → ChromaDB → LLM context

LLM CAPABILITIES IN THIS SYSTEM:
- Answering natural language questions about network state
- Diagnosing issues by correlating topology, metrics, and historical incidents
- Generating structured action plans that route through the human verification gate
- Proactive monitoring: automated alerts when metric thresholds are exceeded

SAFETY CONSTRAINT: All actions that modify device configuration must pass through
the human verification gate regardless of LLM confidence. The LLM suggests; humans approve.
        """.strip()
    },
]


def populate_chromadb(reset: bool = False):
    print("\n── ChromaDB ──────────────────────────────────────")

    # Use sentence-transformers for local embeddings (no API key needed)
    ef = embedding_functions.SentenceTransformerEmbeddingFunction(
        model_name="all-MiniLM-L6-v2"
    )

    client = chromadb.PersistentClient(path=CHROMA_PATH)

    if reset:
        try:
            client.delete_collection(CHROMA_COLLECTION)
            print(f"  Deleted existing collection '{CHROMA_COLLECTION}'")
        except Exception:
            pass

    collection = client.get_or_create_collection(
        name=CHROMA_COLLECTION,
        embedding_function=ef,
        metadata={"hnsw:space": "cosine"}
    )

    # Check existing IDs to avoid duplicates
    existing = set(collection.get()["ids"])

    docs_to_add   = []
    meta_to_add   = []
    ids_to_add    = []

    for doc in DOCUMENTS:
        if doc["id"] in existing and not reset:
            continue
        full_text = f"{doc['title']}\n\n{doc['content']}"
        docs_to_add.append(full_text)
        meta_to_add.append({
            "title":    doc["title"],
            "category": doc["category"],
            "doc_id":   doc["id"],
        })
        ids_to_add.append(doc["id"])

    if docs_to_add:
        collection.add(
            documents=docs_to_add,
            metadatas=meta_to_add,
            ids=ids_to_add,
        )
        print(f"  Added {len(docs_to_add)} documents to collection '{CHROMA_COLLECTION}'")
    else:
        print(f"  All {len(DOCUMENTS)} documents already present — skipping")

    # Verify with a test query
    results = collection.query(query_texts=["switch packet loss"], n_results=2)
    print(f"  Test query 'switch packet loss' → top result: '{results['metadatas'][0][0]['title']}'")
    print("  ✅ ChromaDB done")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Populate digital twin databases with synthetic data")
    parser.add_argument("--reset", action="store_true", help="Wipe and repopulate all databases")
    parser.add_argument("--influx-only", action="store_true")
    parser.add_argument("--neo4j-only",  action="store_true")
    parser.add_argument("--chroma-only", action="store_true")
    args = parser.parse_args()

    run_all = not (args.influx_only or args.neo4j_only or args.chroma_only)

    print("═══════════════════════════════════════════════════")
    print("  Digital Twin — Database Population Script")
    print(f"  Reset mode: {'ON' if args.reset else 'OFF'}")
    print("═══════════════════════════════════════════════════")

    try:
        if run_all or args.influx_only:
            populate_influxdb(reset=args.reset)
    except Exception as e:
        print(f"  ❌ InfluxDB failed: {e}")
        print("     Is InfluxDB running? docker run -d -p 8086:8086 influxdb:2.7")

    try:
        if run_all or args.neo4j_only:
            populate_neo4j(reset=args.reset)
    except Exception as e:
        print(f"  ❌ Neo4j failed: {e}")
        print("     Is Neo4j running? docker run -d -p 7474:7474 -p 7687:7687 -e NEO4J_AUTH=neo4j/password neo4j:5")

    try:
        if run_all or args.chroma_only:
            populate_chromadb(reset=args.reset)
    except Exception as e:
        print(f"  ❌ ChromaDB failed: {e}")
        print("     pip install chromadb sentence-transformers")

    print("\n═══════════════════════════════════════════════════")
    print("  Done. Start the LLM service: python run.py")
    print("═══════════════════════════════════════════════════")


if __name__ == "__main__":
    main()