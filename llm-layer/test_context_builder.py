#!/usr/bin/env python3
from app.core.context_builder import get_context_builder
import pprint

def test_context_builder():
    print("🧪 Testing Context Builder\n")
    
    builder = get_context_builder()
    
    # Test queries
    test_queries = [
        "What's wrong with server-3?",
        "Show me router status",
        "Why is switch-2 degraded?",
        "Explain network digital twin"
    ]
    
    for query in test_queries:
        print(f"\n{'='*60}")
        print(f"QUERY: {query}")
        print(f"{'='*60}")
        
        # Build context
        context = builder.build_context(query)
        
        # Print summary
        print(f"\n📊 Context Summary:")
        print(f"   • Topology nodes: {len(context.topology)}")
        print(f"   • Telemetry points: {len(context.telemetry)}")
        print(f"   • Relevant docs: {len(context.docs)}")
        
        # Print the LLM-friendly summary
        print(f"\n📝 LLM-Ready Context:")
        print("-" * 40)
        print(builder.summarize_context(context))
        print("-" * 40)
        
        input("\nPress Enter to continue...")

if __name__ == "__main__":
    test_context_builder()