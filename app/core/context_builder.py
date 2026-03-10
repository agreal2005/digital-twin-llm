import logging
from typing import List, Dict, Any, Optional
from datetime import datetime, timedelta
from app.utils.config import config

logger = logging.getLogger(__name__)

# Mock data models (we'll move these to models later)
class TopologyNode:
    def __init__(self, id, type, name, status, neighbors=None):
        self.id = id
        self.type = type
        self.name = name
        self.status = status
        self.neighbors = neighbors or []

class TelemetryData:
    def __init__(self, device_id, metric, value, unit, timestamp=None):
        self.device_id = device_id
        self.metric = metric
        self.value = value
        self.unit = unit
        self.timestamp = timestamp or datetime.now()

class SemanticDoc:
    def __init__(self, id, title, content, relevance_score):
        self.id = id
        self.title = title
        self.content = content
        self.relevance_score = relevance_score

class ContextBundle:
    def __init__(self, query, topology=None, telemetry=None, docs=None):
        self.query = query
        self.topology = topology or []
        self.telemetry = telemetry or []
        self.docs = docs or []
        self.timestamp = datetime.now()

class ContextBuilder:
    """Builds context from various data sources."""
    
    def __init__(self):
        logger.info("🔧 Initializing Context Builder (stub mode)")
        # TODO: Initialize real DB connections
        # self.neo4j_driver = Neo4jDriver(...)
        # self.tsdb_client = InfluxDBClient(...)
        # self.vector_db = ChromaDB(...)
    
    def get_topology(self, device_id: Optional[str] = None) -> List[TopologyNode]:
        """
        Retrieve network topology from Neo4j.
        Currently returning stub data.
        """
        logger.debug("Fetching topology (stub)")
        
        # Stub data - realistic network topology
        nodes = [
            TopologyNode(
                id="router-1",
                type="router",
                name="Core Router",
                status="up",
                neighbors=["switch-1", "switch-2", "fw-1"]
            ),
            TopologyNode(
                id="switch-1",
                type="switch",
                name="Access Switch 1",
                status="up",
                neighbors=["router-1", "server-1", "server-2"]
            ),
            TopologyNode(
                id="switch-2",
                type="switch",
                name="Distribution Switch",
                status="degraded",
                neighbors=["router-1", "server-3", "storage-1"]
            ),
            TopologyNode(
                id="server-1",
                type="server",
                name="Web Server",
                status="up",
                neighbors=["switch-1"]
            ),
            TopologyNode(
                id="server-2",
                type="server",
                name="Database Server",
                status="up",
                neighbors=["switch-1"]
            ),
            TopologyNode(
                id="server-3",
                type="server",
                name="Application Server",
                status="down",
                neighbors=["switch-2"]
            ),
            TopologyNode(
                id="fw-1",
                type="firewall",
                name="Edge Firewall",
                status="up",
                neighbors=["router-1", "internet"]
            ),
        ]
        
        # Filter if device_id specified
        if device_id:
            nodes = [n for n in nodes if n.id == device_id or device_id in n.id]
        
        return nodes
    
    def get_telemetry(
        self, 
        device_id: Optional[str] = None,
        metric: Optional[str] = None,
        time_range: timedelta = timedelta(minutes=5)
    ) -> List[TelemetryData]:
        """
        Retrieve telemetry from time-series DB.
        Currently returning stub data.
        """
        logger.debug("Fetching telemetry (stub)")
        
        now = datetime.now()
        telemetry = []
        
        # Devices to fetch telemetry for
        devices = ["router-1", "switch-1", "switch-2", "server-1", "server-2", "server-3"]
        if device_id:
            devices = [device_id]
        
        # Metrics to generate
        metrics_config = [
            ("cpu_usage", "%", lambda d: 45.6 if d != "server-3" else 98.5),
            ("memory_usage", "%", lambda d: 62.3 if d != "server-3" else 95.2),
            ("latency", "ms", lambda d: 12.5 if "router" in d else 5.2),
            ("packet_loss", "%", lambda d: 0.1 if d != "switch-2" else 2.3),
            ("bandwidth_usage", "Mbps", lambda d: 340.2 if "router" in d else 125.7),
        ]
        
        for dev in devices:
            for met_name, unit, value_func in metrics_config:
                if metric and met_name != metric:
                    continue
                    
                telemetry.append(TelemetryData(
                    device_id=dev,
                    metric=met_name,
                    value=value_func(dev),
                    unit=unit,
                    timestamp=now - timedelta(seconds=30)  # Slight offset
                ))
        
        return telemetry
    
    def get_semantic_context(self, query: str, limit: int = 3) -> List[SemanticDoc]:
        """
        Retrieve relevant documentation from vector DB.
        Currently returning stub data based on keyword matching.
        """
        logger.debug(f"Fetching semantic context for: {query[:50]}...")
        
        # Stub documents
        docs = [
            SemanticDoc(
                id="doc-1",
                title="Network Troubleshooting Guide",
                content="When a server shows degraded status, check CPU and memory usage first. Common issues include resource exhaustion, network congestion, and configuration errors. For CPU spikes, identify top processes and check for runaway applications.",
                relevance_score=0.95
            ),
            SemanticDoc(
                id="doc-2",
                title="Router Configuration Best Practices",
                content="Core routers should be configured with OSPF for dynamic routing. Ensure proper ACLs are in place to restrict unauthorized access. Regular backups of configurations are essential for disaster recovery.",
                relevance_score=0.82
            ),
            SemanticDoc(
                id="doc-3",
                title="Switch Troubleshooting",
                content="Switch degradation often indicates spanning tree issues, port flapping, or hardware problems. Check interface counters for errors, CRC errors, and drops. Verify VLAN configurations and trunk ports.",
                relevance_score=0.78
            ),
            SemanticDoc(
                id="doc-4",
                title="Server Performance Monitoring",
                content="Monitor CPU, memory, disk I/O, and network interfaces. Alert on sustained high utilization. For database servers, pay special attention to connection counts and query performance.",
                relevance_score=0.91
            ),
            SemanticDoc(
                id="doc-5",
                title="Network Digital Twin Concepts",
                content="A network digital twin is a virtual representation of the physical network that enables simulation, prediction, and autonomous control. It combines real-time telemetry with historical data and AI/ML models.",
                relevance_score=0.88
            ),
        ]
        
        # Simple keyword-based relevance (stub RAG)
        keywords = query.lower().split()
        relevant_docs = []
        
        for doc in docs:
            score = 0
            content_lower = doc.content.lower()
            title_lower = doc.title.lower()
            
            for kw in keywords:
                if len(kw) < 3:  # Skip short words
                    continue
                if kw in title_lower:
                    score += 0.3
                if kw in content_lower:
                    score += 0.2
            
            if score > 0:
                doc.relevance_score = min(1.0, score)
                relevant_docs.append((score, doc))
        
        # Sort by relevance and return top matches
        relevant_docs.sort(key=lambda x: x[0], reverse=True)
        return [doc for score, doc in relevant_docs[:limit]]
    
    def build_context(self, query: str) -> ContextBundle:
        """
        Aggregate all context for a given query.
        """
        logger.info(f"🔍 Building context for query: {query[:50]}...")
        
        # Extract potential device mentions from query (simple stub)
        devices = ["router", "switch", "server", "firewall"]
        mentioned_device = None
        for device in devices:
            if device in query.lower():
                mentioned_device = device
                break
        
        # Get data
        topology = self.get_topology(device_id=mentioned_device)
        telemetry = self.get_telemetry(device_id=mentioned_device)
        docs = self.get_semantic_context(query)
        
        bundle = ContextBundle(
            query=query,
            topology=topology,
            telemetry=telemetry,
            docs=docs
        )
        
        logger.info(f"✅ Context built: {len(topology)} nodes, {len(telemetry)} metrics, {len(docs)} docs")
        return bundle
    
    def summarize_context(self, bundle: ContextBundle) -> str:
        """
        Convert the context bundle into LLM-friendly text.
        """
        lines = []
        lines.append("=== NETWORK TOPOLOGY ===")
        status_emojis = {"up": "✅", "degraded": "⚠️", "down": "❌", "unknown": "❓"}
        
        for node in bundle.topology:
            emoji = status_emojis.get(node.status, "❓")
            lines.append(f"{emoji} {node.name} ({node.type}): {node.status}")
            if node.neighbors:
                lines.append(f"   Connected to: {', '.join(node.neighbors[:3])}")
        
        if bundle.telemetry:
            lines.append("\n=== CURRENT TELEMETRY ===")
            # Group by device
            tele_by_device = {}
            for t in bundle.telemetry:
                if t.device_id not in tele_by_device:
                    tele_by_device[t.device_id] = []
                tele_by_device[t.device_id].append(t)
            
            for device, metrics in list(tele_by_device.items())[:3]:  # Limit to 3 devices
                lines.append(f"\n{device}:")
                for m in metrics[:3]:  # Limit to 3 metrics per device
                    lines.append(f"   • {m.metric}: {m.value:.1f} {m.unit}")
        
        if bundle.docs:
            lines.append("\n=== RELEVANT DOCUMENTATION ===")
            for doc in bundle.docs[:2]:
                lines.append(f"\n📄 {doc.title}:")
                # Truncate content
                content = doc.content[:150] + "..." if len(doc.content) > 150 else doc.content
                lines.append(f"   {content}")
        
        return "\n".join(lines)

# Singleton
_context_builder = None

def get_context_builder():
    global _context_builder
    if _context_builder is None:
        _context_builder = ContextBuilder()
    return _context_builder