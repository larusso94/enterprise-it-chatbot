"""
Cosmos DB client for CU1 IT Chatbot with vector search capabilities.

This module provides comprehensive Cosmos DB functionality including:
- Session management for conversation history
- Vector search for knowledge base queries
- Azure OpenAI embeddings integration
- Multi-container support (sessions and vectors)
- Connection resilience and error handling

Features:
- Automatic container creation
- Cosine similarity-based semantic search
- Embeddings with fallback for testing
- Structured logging and error handling
"""

import time
import math
from typing import Optional, Any, Dict, List
from .logging_client import log
from ..mcp.config import config
from azure.cosmos import CosmosClient, PartitionKey
from openai import AzureOpenAI

class EmbeddingsClient:
    """
    Azure OpenAI embeddings client with fallback support.
    
    Provides text embedding functionality for semantic search using Azure OpenAI
    with deterministic fallback vectors for testing environments where OpenAI
    is not available.
    
    Features:
    - Azure OpenAI embeddings API integration
    - Deterministic fallback vectors for testing
    - Error handling and graceful degradation
    - Configurable embedding dimensions
    """
    
    def __init__(self):
        self.endpoint = config.openai_api_base
        self.api_key = config.openai_api_key
        self.deployment = config.openai_embed_deployment
        self.api_version = config.openai_api_version
        self.dim = config.embedding_dim
        
        self._client = None
        
        # Try to initialize Azure OpenAI client
        if self.endpoint and self.api_key and self.deployment:
            try:
                self._client = AzureOpenAI(
                    api_key=self.api_key,
                    api_version=self.api_version,
                    azure_endpoint=self.endpoint,
                )
                log.info("embeddings.client.init.success", 
                        deployment=self.deployment)
            except Exception as e:
                log.error("embeddings.client.init.failed", 
                         error=str(e))
                self._client = None

    def embed_text(self, text: str) -> List[float]:
        """
        Generate embedding vector for a single text string.
        
        Args:
            text: Input text to embed
            
        Returns:
            List[float]: Embedding vector of configured dimensions
        """
        if not text:
            return self._fallback_vector(text)
            
        try:
            if self._client:
                response = self._client.embeddings.create(
                    model=self.deployment,
                    input=[text]
                )
                return response.data[0].embedding
            else:
                return self._fallback_vector(text)
                
        except Exception as e:
            log.error("embeddings.embed_text.error", 
                     error=str(e))
            return self._fallback_vector(text)

    def _fallback_vector(self, text: str) -> List[float]:
        """
        Generate deterministic fallback vector when OpenAI is unavailable.
        
        Creates consistent vectors based on text content for scenarios where
        Azure OpenAI service cannot be reached.
        
        Args:
            text: Input text to create vector for
            
        Returns:
            List[float]: Deterministic vector based on text content
        """
        base = sum(ord(c) for c in text) or 1
        prime_base = config.embedding_fallback_prime_base
        return [((base * (j + 1)) % prime_base) / prime_base for j in range(self.dim)]


class CosmosDBClient:

    def __init__(self):
        """Initialize Cosmos DB client without blocking runtime."""
        cfg = config
        self.endpoint = cfg.cosmos_endpoint
        self.key = cfg.cosmos_key
        self.database_name = cfg.cosmos_database
        self.container_sessions = cfg.cosmos_container_sessions
        self.container_vectors = cfg.cosmos_container_vectors
        self.partition_key = cfg.partition_key
        
        # Initialize as disconnected
        self.client = None
        self.database = None
        self.sessions_container = None
        self.vectors_container = None
        self.connected = False
        
        # Initialize embeddings client
        self.embeddings = EmbeddingsClient()
        
        # Log configuration details for debugging
        self._log_configuration_details()
        
        # Attempt connection without stopping runtime
        self._try_connect()
    
    def _log_configuration_details(self):
        """Log detailed configuration for debugging connection issues."""
        import os
        
        # Check environment variables directly
        env_endpoint = os.getenv("cosmos-endpoint")
        env_key = os.getenv("cosmos-key")
        env_database = os.getenv("cosmos-database")
        
        log.info("cosmos.config.details",
                # Configuration from config object
                config_endpoint=self.endpoint[:50] + "..." if self.endpoint and len(self.endpoint) > 50 else self.endpoint,
                config_has_key=bool(self.key),
                config_database=self.database_name,
                config_sessions_container=self.container_sessions,
                config_vectors_container=self.container_vectors,
                config_partition_key=self.partition_key,
                
                # Environment variables check
                env_endpoint_set=bool(env_endpoint),
                env_key_set=bool(env_key),
                env_database_set=bool(env_database),
                
                # Previous connection state (if any)
                currently_connected=getattr(self, 'connected', False))
        
        # Log potential configuration issues
        issues = []
        if not self.endpoint:
            issues.append("missing_endpoint")
        if not self.key:
            issues.append("missing_key")
        if not self.database_name:
            issues.append("missing_database_name")
            
        if issues:
            log.warning("cosmos.config.issues", issues=issues)
        
    def _try_connect(self):
        """Attempt to connect to Cosmos DB without blocking runtime."""
        # Check critical configuration
        if not self.endpoint or not self.key or not self.database_name:
            log.warning("cosmos.config.incomplete", 
                       has_endpoint=bool(self.endpoint),
                       has_key=bool(self.key), 
                       has_database=bool(self.database_name))
            return
            
        try:
            # Create client with key or managed identity
            log.info("cosmos.connection.step", step="creating_client", auth_method="key" if self.key else "managed_identity")
            
            self.client = CosmosClient(self.endpoint, credential=self.key)

            log.info("cosmos.connection.step", step="client_created")
            
            # Create database and containers if they don't exist
            log.info("cosmos.connection.step", step="creating_database", database_name=self.database_name)
            self.database = self.client.create_database_if_not_exists(id=self.database_name)
            
            log.info("cosmos.connection.step", step="creating_containers")
            self.sessions_container = self.database.create_container_if_not_exists(
                id=self.container_sessions, 
                partition_key=PartitionKey(path="/user_email")
            )
            self.vectors_container = self.database.create_container_if_not_exists(
                id=self.container_vectors,
                partition_key=PartitionKey(path=self.partition_key)
            )
            
            # Quick connectivity test
            log.info("cosmos.connection.step", step="connectivity_test")
            containers = list(self.database.list_containers())
            self.connected = True
            
            log.info("cosmos.connection.success",
                    endpoint=self.endpoint[:50] + "..." if len(self.endpoint) > 50 else self.endpoint,
                    database=self.database_name,
                    containers_found=len(containers))
                    
        except Exception as e:
            error_msg = str(e)
            error_type = type(e).__name__
            
            # Diagnose specific error types
            diagnosis = self._diagnose_connection_error(error_msg, error_type)
            
            log.error("cosmos.connection.failed", 
                     error=error_msg[:300],
                     error_type=error_type,
                     endpoint=self.endpoint[:50] + "..." if self.endpoint else "none",
                     database_name=self.database_name,
                     has_key=bool(self.key),
                     diagnosis=diagnosis)
    
    def _diagnose_connection_error(self, error_msg: str, error_type: str) -> str:
        """Diagnose specific Cosmos DB connection errors."""
        error_lower = error_msg.lower()
        
        if "unauthorized" in error_lower or "401" in error_msg:
            return "authentication_failed - Check Cosmos DB key or managed identity permissions"
        elif "forbidden" in error_lower or "403" in error_msg:
            return "access_denied - Insufficient permissions on Cosmos DB account"
        elif "not found" in error_lower or "404" in error_msg:
            return "resource_not_found - Cosmos DB account or database may not exist"
        elif "timeout" in error_lower or "connection" in error_lower:
            return "network_issue - Connection timeout or network connectivity problem"
        elif "ssl" in error_lower or "certificate" in error_lower:
            return "ssl_issue - SSL/TLS certificate problem"
        elif "dns" in error_lower or "name resolution" in error_lower:
            return "dns_issue - Cannot resolve Cosmos DB endpoint hostname"
        elif "firewall" in error_lower or "blocked" in error_lower:
            return "firewall_issue - Connection blocked by firewall or network policies"
        elif "quota" in error_lower or "limit" in error_lower:
            return "quota_exceeded - Cosmos DB quota or rate limits exceeded"
        elif "service unavailable" in error_lower or "503" in error_msg:
            return "service_unavailable - Cosmos DB service temporarily unavailable"
        elif error_type == "ValueError" and "credential" in error_lower:
            return "credential_format_error - Invalid credential format or missing credentials"
        else:
            return f"unknown_error - {error_type}: Check Cosmos DB configuration and connectivity"
            
    def is_available(self) -> bool:
        available = self.connected and self.client is not None
        
        if not available:
            log.warning("cosmos.availability.check_failed",
                       connected=self.connected,
                       has_client=bool(self.client),
                       has_database=bool(getattr(self, 'database', None)),
                       has_sessions_container=bool(getattr(self, 'sessions_container', None)),
                       has_vectors_container=bool(getattr(self, 'vectors_container', None)),
                       endpoint=self.endpoint[:50] + "..." if self.endpoint and len(self.endpoint) > 50 else self.endpoint,
                       has_key=bool(self.key),
                       database_name=self.database_name)
        return available

    def get_session(self, user_email: Optional[str] = None) -> Any:
        """Get session data for user."""
        if not self.is_available():
            return None
            
        if not user_email:
            return None
        
        try:
            # Direct item read using user_email as both id and partition key
            item = self.sessions_container.read_item(item=user_email, partition_key=user_email)
            return item
                
        except Exception as e:
            error_type = type(e).__name__
            error_msg = str(e)
            
            # Item not found (new user) - this is expected behavior
            if "NotFound" in error_type or "404" in error_msg:
                return None  # Return None for new users
            else:
                # Unexpected error
                log.error("cosmos.session.get.error", 
                         user_email=user_email, 
                         error=error_msg[:200])
                return None

    def save_session(self, user_email: Optional[str], trace: List[Dict[str, Any]] = None, **kwargs) -> str:
        """Save session data for user."""
        if not self.is_available():
            raise ValueError("Cosmos DB not available for session persistence")
            
        if not user_email:
            raise ValueError("user_email is required for session persistence")
        
        if trace is None:
            trace = []
            
        # Create item with user_email as both id and partition key
        # Include trace (conversation history) and any additional data
        item = {
            "id": user_email, 
            "user_email": user_email, 
            "trace": trace,
            "last_updated": time.time(),
            **kwargs  # Any additional fields
        }
        
        try:
            self.sessions_container.upsert_item(item)
            log.info("cosmos.session.save.success", user_email=user_email)
        except Exception as e:
            log.error("cosmos.session.save.error", 
                     user_email=user_email, 
                     error=str(e)[:200])
            raise
        return user_email

    def vector_search(self, query: str, top_k: int = None) -> List[Dict[str, Any]]:
        """Semantic vector search in the knowledge base."""
        if top_k is None:
            top_k = config.vector_search_top_k
            
        if not self.is_available():
            log.warning("cosmos.vector_search.unavailable")
            return []
            
        if not query.strip():
            log.warning("cosmos.vector_search.empty_query")
            return []
        
        try:
            # Generate embedding for the query
            query_vector = self.embeddings.embed_text(query)
            
            # Get all documents from vector store
            all_docs = list(self.vectors_container.query_items(
                query="SELECT * FROM c",
                enable_cross_partition_query=True
            ))
            
            if not all_docs:
                log.info("cosmos.vector_search.no_documents")
                return []
            
            # Calculate cosine similarity for each document
            scored_docs = []
            for doc in all_docs:
                if "vector" not in doc or not doc["vector"]:
                    continue
                    
                similarity = self._cosine_similarity(query_vector, doc["vector"])
                scored_docs.append({
                    "score": similarity,
                    "document": doc
                })
            
            # Sort by similarity score (highest first) and take top_k
            scored_docs.sort(key=lambda x: x["score"], reverse=True)
            top_results = scored_docs[:top_k]
            
            # Format results - only essential attributes
            results = []
            for item in top_results:
                doc = item["document"]
                result = {
                    "number": doc.get("number", ""),
                    "title": doc.get("title", ""),
                    "content": doc.get("text_chunk", ""),
                    "score": round(item["score"], 4)
                }
                
                # Add essential metadata only
                if "metadata" in doc:
                    metadata = doc["metadata"]
                    if "article_url" in metadata:
                        result["article_url"] = metadata["article_url"]
                    if "chunk_type" in metadata:
                        result["chunk_type"] = metadata["chunk_type"]
                    if "is_summary" in metadata:
                        result["is_summary"] = metadata["is_summary"]
                
                results.append(result)
            
            log.info("cosmos.vector_search.success",
                    total_docs=len(all_docs),
                    results_count=len(results),
                    top_score=results[0]["score"] if results else 0)
            
            return results
            
        except Exception as e:
            log.error("cosmos.vector_search.error",
                     error=str(e))
            return []

    def _cosine_similarity(self, vec1: List[float], vec2: List[float]) -> float:
        """Calculate cosine similarity between two vectors."""
        try:
            # Ensure vectors have the same length
            min_len = min(len(vec1), len(vec2))
            if min_len == 0:
                return 0.0
                
            v1 = vec1[:min_len]
            v2 = vec2[:min_len]
            
            # Calculate dot product
            dot_product = sum(a * b for a, b in zip(v1, v2))
            
            # Calculate magnitudes
            magnitude1 = math.sqrt(sum(a * a for a in v1))
            magnitude2 = math.sqrt(sum(a * a for a in v2))
            
            # Avoid division by zero
            if magnitude1 == 0 or magnitude2 == 0:
                return 0.0
                
            # Calculate cosine similarity
            similarity = dot_product / (magnitude1 * magnitude2)
            
            # Clamp to [-1, 1] range due to floating point precision
            return max(-1.0, min(1.0, similarity))
            
        except Exception as e:
            log.error("cosmos.cosine_similarity.error", error=str(e))
            return 0.0

    def load_agent_sysprompt(self, version: Optional[str] = None) -> Dict[str, Any]:
        """Load agent system prompt from Cosmos DB."""
        if not self.is_available():
            return {}
            
        try:
            # Try to get sysprompt container (if it exists)
            sysprompt_container = self.database.get_container_client("sysprompt")
            
            if version:
                query = f"SELECT * FROM c WHERE c.version = '{version}'"
            else:
                query = "SELECT * FROM c ORDER BY c.version DESC"
                
            items = list(sysprompt_container.query_items(
                query=query, 
                enable_cross_partition_query=True
            ))
            
            if items:
                log.info("cosmos.sysprompt.success", version=version)
                return items[0].get("spec", {})
                
        except Exception as e:
            log.error("cosmos.sysprompt.error", 
                     error=str(e),
                     version=version)
        
        return {}
