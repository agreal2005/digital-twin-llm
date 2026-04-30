#!/usr/bin/env python3
"""
ChromaDB Telnet Bridge - Simplified JSON-only version
Supports: query, add, delete with folder management
"""

import socket
import threading
import json
import chromadb
from chromadb.utils.embedding_functions import SentenceTransformerEmbeddingFunction
from datetime import datetime
import logging
import signal
import sys
import os

class ChromaDBTelnetBridge:
    def __init__(self, telnet_port=12346, db_path="./chroma_db"):
        self.telnet_port = telnet_port
        self.db_path = db_path
        self.server_running = True
        self.clients = []
        
        # Setup logging
        logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
        self.logger = logging.getLogger(__name__)
        
        # Initialize ChromaDB
        self.init_chromadb()
        
        # Handle Ctrl+C
        signal.signal(signal.SIGINT, self.signal_handler)
    
    def init_chromadb(self):
        """Initialize persistent ChromaDB with folder support"""
        try:
            self.client = chromadb.PersistentClient(path=self.db_path)
            self.embedding_fn = SentenceTransformerEmbeddingFunction(model_name="all-MiniLM-L6-v2")
            
            # Dictionary to store collections (folders)
            self.collections = {}
            
            # Load existing collections
            try:
                existing_collections = self.client.list_collections()
                for coll in existing_collections:
                    self.collections[coll.name] = coll
                    self.logger.info(f"Loaded folder: {coll.name} ({coll.count()} docs)")
            except:
                pass
            
            self.logger.info(f"ChromaDB initialized at {self.db_path}")
            
        except Exception as e:
            self.logger.error(f"Failed to initialize ChromaDB: {e}")
            sys.exit(1)
    
    def get_collection(self, folder_name):
        """Get or create a collection (folder)"""
        if folder_name not in self.collections:
            self.collections[folder_name] = self.client.get_or_create_collection(
                name=folder_name,
                embedding_function=self.embedding_fn,
                metadata={"description": f"Folder: {folder_name}"}
            )
            self.logger.info(f"Created new folder: {folder_name}")
        return self.collections[folder_name]
    
    def signal_handler(self, sig, frame):
        print("\n\nShutting down...")
        self.server_running = False
        for client in self.clients:
            try:
                client.close()
            except:
                pass
        sys.exit(0)
    
    def send_json(self, sock, response):
        """Send JSON response with newline"""
        try:
            sock.send((json.dumps(response) + "\n").encode())
        except:
            pass
    
    def process_query(self, sock, data):
        """Process query operation - returns full content when requested"""
        try:
            query_text = data.get("content", "")
            threshold = float(data.get("threshold", 0.1))
            number = int(data.get("number", 10))
            folder = data.get("folder", "default")
            include_full = data.get("full", False)  # Parameter to request full content
            
            collection = self.get_collection(folder)
            
            # If query_text is empty, get all documents
            if not query_text or query_text.strip() == "":
                # Get all documents in the folder
                all_docs = collection.get()
                formatted_results = []
                
                if all_docs['ids']:
                    for i in range(len(all_docs['ids'])):
                        metadata = all_docs['metadatas'][i] if all_docs['metadatas'] else {}
                        full_content = all_docs['documents'][i] if all_docs['documents'] else ""
                        
                        formatted_results.append({
                            "id": all_docs['ids'][i],
                            "title": metadata.get('title', 'Unknown'),
                            "similarity": 1.0,  # No similarity for direct access
                            "content": full_content[:500] if not include_full else full_content,
                            "full_content": full_content if include_full else None
                        })
                
                self.send_json(sock, {
                    "status": "success",
                    "type": "query_result",
                    "folder": folder,
                    "query": "all_documents",
                    "threshold": threshold,
                    "results_count": len(formatted_results),
                    "results": formatted_results
                })
                return
            
            # Normal query with search
            results = collection.query(
                query_texts=[query_text],
                n_results=number
            )
            
            # Format results
            formatted_results = []
            if results['documents'] and results['documents'][0]:
                for i in range(len(results['documents'][0])):
                    distance = results['distances'][0][i] if results['distances'] else 1.0
                    similarity = 1 - distance
                    
                    if similarity >= threshold:
                        metadata = results['metadatas'][0][i] if results['metadatas'] else {}
                        full_content = results['documents'][0][i]
                        
                        formatted_results.append({
                            "id": results['ids'][0][i],
                            "title": metadata.get('title', 'Unknown'),
                            "similarity": round(similarity, 4),
                            "content": full_content[:500] if not include_full else full_content,
                            "full_content": full_content if include_full else None
                        })
            
            self.send_json(sock, {
                "status": "success",
                "type": "query_result",
                "folder": folder,
                "query": query_text,
                "threshold": threshold,
                "results_count": len(formatted_results),
                "results": formatted_results
            })
            
        except Exception as e:
            self.send_json(sock, {"status": "error", "message": str(e)})
    
    def process_add(self, sock, data):
        """Process add operation"""
        try:
            title = data.get("title", "")
            content = data.get("content", "")
            folder = data.get("folder", "default")
            
            if not title or not content:
                self.send_json(sock, {"status": "error", "message": "title and content required"})
                return
            
            collection = self.get_collection(folder)
            
            # Generate unique ID
            doc_id = f"{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}"
            metadata = {
                "title": title,
                "folder": folder,
                "timestamp": datetime.now().isoformat()
            }
            
            collection.add(
                ids=[doc_id],
                documents=[content],
                metadatas=[metadata]
            )
            
            self.send_json(sock, {
                "status": "success",
                "type": "add_result",
                "id": doc_id,
                "title": title,
                "folder": folder,
                "message": f"Document added successfully"
            })
            
        except Exception as e:
            self.send_json(sock, {"status": "error", "message": str(e)})
    
    def process_delete(self, sock, data):
        """Process delete operation"""
        try:
            title = data.get("title", "")
            folder = data.get("folder", None)
            
            if title == "*":
                # Delete entire database
                if folder:
                    # Delete specific folder
                    if folder in self.collections:
                        # Delete all documents in folder
                        all_docs = self.collections[folder].get()
                        if all_docs['ids']:
                            self.collections[folder].delete(ids=all_docs['ids'])
                        self.send_json(sock, {
                            "status": "success",
                            "type": "delete_result",
                            "message": f"Deleted all documents in folder '{folder}'",
                            "deleted_count": len(all_docs['ids'])
                        })
                    else:
                        self.send_json(sock, {"status": "error", "message": f"Folder '{folder}' not found"})
                else:
                    # Delete ALL folders and documents
                    total_deleted = 0
                    for folder_name, collection in self.collections.items():
                        all_docs = collection.get()
                        if all_docs['ids']:
                            collection.delete(ids=all_docs['ids'])
                            total_deleted += len(all_docs['ids'])
                    
                    # Clear collections dict
                    self.collections.clear()
                    
                    self.send_json(sock, {
                        "status": "success",
                        "type": "delete_result",
                        "message": "Deleted ALL documents from ALL folders",
                        "deleted_count": total_deleted
                    })
            else:
                # Delete specific document by title
                if not folder:
                    self.send_json(sock, {"status": "error", "message": "folder required for title-based delete"})
                    return
                
                if folder not in self.collections:
                    self.send_json(sock, {"status": "error", "message": f"Folder '{folder}' not found"})
                    return
                
                # Search for document with matching title
                collection = self.collections[folder]
                all_docs = collection.get()
                
                found_ids = []
                for i, metadata in enumerate(all_docs['metadatas']):
                    if metadata and metadata.get('title') == title:
                        found_ids.append(all_docs['ids'][i])
                
                if found_ids:
                    collection.delete(ids=found_ids)
                    self.send_json(sock, {
                        "status": "success",
                        "type": "delete_result",
                        "message": f"Deleted {len(found_ids)} document(s) with title '{title}'",
                        "deleted_ids": found_ids
                    })
                else:
                    self.send_json(sock, {"status": "error", "message": f"Document with title '{title}' not found in folder '{folder}'"})
            
        except Exception as e:
            self.send_json(sock, {"status": "error", "message": str(e)})
    
    def process_stats(self, sock, data):
        """Process stats operation"""
        try:
            folder = data.get("folder", None)
            
            if folder:
                # Stats for specific folder
                if folder in self.collections:
                    count = self.collections[folder].count()
                    self.send_json(sock, {
                        "status": "success",
                        "type": "stats",
                        "folder": folder,
                        "document_count": count
                    })
                else:
                    self.send_json(sock, {"status": "error", "message": f"Folder '{folder}' not found"})
            else:
                # Stats for all folders
                all_stats = {}
                total_docs = 0
                for folder_name, collection in self.collections.items():
                    count = collection.count()
                    all_stats[folder_name] = count
                    total_docs += count
                
                self.send_json(sock, {
                    "status": "success",
                    "type": "stats",
                    "total_documents": total_docs,
                    "total_folders": len(self.collections),
                    "folders": all_stats
                })
                
        except Exception as e:
            self.send_json(sock, {"status": "error", "message": str(e)})
    
    def process_list_folders(self, sock, data):
        """List all folders"""
        try:
            folders = list(self.collections.keys())
            self.send_json(sock, {
                "status": "success",
                "type": "folder_list",
                "folders": folders,
                "count": len(folders)
            })
        except Exception as e:
            self.send_json(sock, {"status": "error", "message": str(e)})
    
    def handle_client(self, client_socket, client_address):
        """Handle client connection (supports both telnet and JSON)"""
        self.logger.info(f"Client connected: {client_address}")
        self.clients.append(client_socket)
        
        try:
            # Send welcome banner
            welcome = {
                "status": "ready",
                "message": "ChromaDB Bridge Ready",
                "commands": {
                    "query": {"type": "query", "content": "text", "threshold": 0.1, "number": 10, "folder": "name", "full": False},
                    "add": {"type": "add", "title": "title", "content": "text", "folder": "name"},
                    "delete": {"type": "delete", "title": "title or *", "folder": "name"},
                    "stats": {"type": "stats", "folder": "name (optional)"},
                    "list": {"type": "list"},
                    "help": {"type": "help"}
                },
                "example_query": '{"type":"query","content":"network config","threshold":0.1,"number":5,"folder":"configs"}',
                "example_query_full": '{"type":"query","content":"","threshold":0.0,"number":1,"folder":"configs","full":true}',
                "example_add": '{"type":"add","title":"My Doc","content":"Document content here","folder":"docs"}',
                "example_delete": '{"type":"delete","title":"My Doc","folder":"docs"}',
                "example_delete_all": '{"type":"delete","title":"*"}',
                "example_delete_folder": '{"type":"delete","title":"*","folder":"docs"}'
            }
            self.send_json(client_socket, welcome)
            
            # Buffer for partial reads
            buffer = ""
            
            while self.server_running:
                try:
                    client_socket.settimeout(1.0)
                    data = client_socket.recv(65536)  # Increased buffer size
                    if not data:
                        break
                    
                    buffer += data.decode('utf-8')
                    
                    # Process complete lines
                    while '\n' in buffer:
                        line, buffer = buffer.split('\n', 1)
                        line = line.strip()
                        
                        if not line:
                            continue
                        
                        # Parse JSON
                        try:
                            command = json.loads(line)
                            cmd_type = command.get("type", "").lower()
                            
                            if cmd_type == "query":
                                self.process_query(client_socket, command)
                            elif cmd_type == "add":
                                self.process_add(client_socket, command)
                            elif cmd_type == "delete":
                                self.process_delete(client_socket, command)
                            elif cmd_type == "stats":
                                self.process_stats(client_socket, command)
                            elif cmd_type == "list":
                                self.process_list_folders(client_socket, command)
                            elif cmd_type == "help":
                                self.send_json(client_socket, welcome)
                            else:
                                self.send_json(client_socket, {
                                    "status": "error", 
                                    "message": f"Unknown type: {cmd_type}. Available: query, add, delete, stats, list, help"
                                })
                        except json.JSONDecodeError as e:
                            self.send_json(client_socket, {
                                "status": "error", 
                                "message": f"Invalid JSON: {str(e)}",
                                "received": line
                            })
                            
                except socket.timeout:
                    continue
                except Exception as e:
                    self.logger.error(f"Error handling client: {e}")
                    break
                    
        except Exception as e:
            self.logger.error(f"Client handler error: {e}")
        finally:
            if client_socket in self.clients:
                self.clients.remove(client_socket)
            client_socket.close()
            self.logger.info(f"Client disconnected: {client_address}")
    
    def run(self):
        """Run the server"""
        server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        
        try:
            server.bind(('0.0.0.0', self.telnet_port))
        except OSError as e:
            self.logger.error(f"Port {self.telnet_port} in use: {e}")
            print(f"\n❌ Port {self.telnet_port} is in use.")
            return
        
        server.listen(10)
        
        print(f"\n{'='*60}")
        print(f"✅ ChromaDB Bridge Running on port {self.telnet_port}")
        print(f"📁 Database: {self.db_path}")
        print(f"\n📡 Connect with: telnet localhost {self.telnet_port}")
        print(f"💡 Send JSON commands (one per line)")
        print(f"{'='*60}\n")
        
        try:
            while self.server_running:
                server.settimeout(1.0)
                try:
                    client, addr = server.accept()
                    client.settimeout(None)
                    thread = threading.Thread(target=self.handle_client, args=(client, addr), daemon=True)
                    thread.start()
                except socket.timeout:
                    continue
                except Exception as e:
                    self.logger.error(f"Accept error: {e}")
                    
        except KeyboardInterrupt:
            self.logger.info("Shutting down...")
        finally:
            server.close()

def main():
    TELNET_PORT = 12346
    DB_PATH = "./chroma_db"
    
    bridge = ChromaDBTelnetBridge(
        telnet_port=TELNET_PORT,
        db_path=DB_PATH
    )
    
    bridge.run()

if __name__ == "__main__":
    main()