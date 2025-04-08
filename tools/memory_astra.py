import os
import asyncio
import logging
from typing import Optional, Dict, Any
from astrapy.db import AstraDB, AstraDBCollection
from dotenv import load_dotenv

# Import the local embedding generator
from .local_embeddings import LocalEmbeddingGenerator

# --- Configuration ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Load environment variables - Ensure this runs before accessing variables
load_dotenv(override=True)

# Pull configuration from environment variables with defaults
ASTRA_DB_API_ENDPOINT = os.getenv("ASTRA_DB_API_ENDPOINT")
ASTRA_DB_APPLICATION_TOKEN = os.getenv("ASTRA_DB_APPLICATION_TOKEN")
ASTRA_DB_NAMESPACE = os.getenv("ASTRA_DB_NAMESPACE", "default_keyspace") # Use namespace from env or default
COLLECTION_NAME = os.getenv("ASTRA_MEMORY_COLLECTION", "memory_store") # Configurable collection name
EMBEDDING_DIMENSION = 512 # Dimension from LocalEmbeddingGenerator
VECTOR_SEARCH_LIMIT = 3 # How many results to return from vector search

# --- Global State (lazily initialized) ---
db_instance: Optional[AstraDB] = None
collection_instance: Optional[AstraDBCollection] = None
db_lock = asyncio.Lock() # Lock to prevent race conditions during initialization
embedding_generator = LocalEmbeddingGenerator(vector_size=EMBEDDING_DIMENSION)

# --- Helper Function for Initialization and Connection ---

async def _initialize_astra_db_resources() -> Optional[AstraDBCollection]:
    """
    Initializes the AstraDB connection and collection instance asynchronously and safely.
    Uses a lock to prevent race conditions during concurrent initializations.
    Returns the collection instance or None if initialization fails.
    """
    global db_instance, collection_instance

    # Fast path: Already initialized
    if collection_instance:
        return collection_instance

    # Acquire lock to ensure only one coroutine initializes at a time
    async with db_lock:
        # Check again inside the lock in case it was initialized while waiting
        if collection_instance:
            return collection_instance

        # Check for credentials
        if not ASTRA_DB_API_ENDPOINT or not ASTRA_DB_APPLICATION_TOKEN:
            logger.critical("AstraDB credentials (API Endpoint or Token) not found in environment variables. Cannot initialize.")
            return None

        loop = asyncio.get_running_loop()
        try:
            # Initialize DB connection if not already done
            if not db_instance:
                logger.info("Initializing AstraDB connection...")
                db_instance = await loop.run_in_executor(
                    None, # Use default executor (thread pool)
                    lambda: AstraDB(
                        api_endpoint=ASTRA_DB_API_ENDPOINT,
                        token=ASTRA_DB_APPLICATION_TOKEN,
                        namespace=ASTRA_DB_NAMESPACE
                    )
                )
                logger.info("AstraDB connection initialized.")

            # Create or get the collection with vector support asynchronously
            logger.info(f"Accessing/Creating collection '{COLLECTION_NAME}' with vector dimension {EMBEDDING_DIMENSION}...")
            # Note: create_collection is idempotent but better to run in executor
            collection_instance = await loop.run_in_executor(
                None,
                lambda: db_instance.create_collection( # type: ignore[union-attr] # Assume db_instance is not None here
                    collection_name=COLLECTION_NAME,
                    dimension=EMBEDDING_DIMENSION,
                    metric="cosine" # Common metric for embeddings
                )
            )
            logger.info(f"Collection '{COLLECTION_NAME}' accessed/created successfully.")
            return collection_instance

        except Exception as e:
            # Catch specific Astra exceptions if they exist and are useful, otherwise broad Exception
            logger.error(f"CRITICAL: Failed to initialize AstraDB or collection '{COLLECTION_NAME}': {e}", exc_info=True)
            # Reset global state on failure
            db_instance = None
            collection_instance = None
            return None

# --- Tool Functions ---

async def store_memory(text_to_store: str) -> str:
    """
    Stores the given text and its vector embedding asynchronously and robustly in AstraDB.
    """
    if not text_to_store or not text_to_store.strip():
        logger.warning("Attempted to store empty memory.")
        return "Error: Cannot store empty memory."

    logger.info(f"Attempting to store memory: '{text_to_store[:50]}...'")
    collection = await _initialize_astra_db_resources()
    if not collection:
        # Initialization failed, critical error logged in _initialize_astra_db_resources
        return "Error: Could not connect to or initialize the memory database."

    loop = asyncio.get_running_loop()
    try:
        # 1. Generate embedding (CPU-bound, but quick enough perhaps not needing executor)
        logger.debug("Generating embedding...")
        vector = embedding_generator.get_embedding(text_to_store)
        if not vector or len(vector) != EMBEDDING_DIMENSION:
             logger.error(f"Failed to generate valid embedding for text: '{text_to_store[:50]}...'")
             return "Error: Failed to process text for memory storage (embedding error)."
        logger.debug("Embedding generated.")

        # 2. Prepare document
        document: Dict[str, Any] = {
            "text": text_to_store,
            "$vector": vector
            # Could add timestamp: "timestamp": datetime.utcnow().isoformat()
        }

        # 3. Insert into AstraDB asynchronously
        logger.debug(f"Inserting document into '{COLLECTION_NAME}'...")
        insert_result = await loop.run_in_executor(
            None, # Use default executor
            lambda: collection.insert_one(document)
        )

        inserted_ids = insert_result.get('status', {}).get('insertedIds', [])
        if inserted_ids:
            logger.info(f"Memory stored successfully with ID: {inserted_ids[0]}")
            return "Memory stored successfully."
        else:
            # Log the full response if insert seemed to succeed but no ID returned
            logger.error(f"Memory storage command did not return an ID. Full response: {insert_result}")
            return "Error: Stored memory, but confirmation failed."

    except Exception as e:
        # Catch potential errors during embedding or insertion
        logger.error(f"Error storing memory to AstraDB: {e}", exc_info=True)
        # Consider checking for specific DB exceptions if needed (e.g., timeout, connection error)
        return f"Error: Could not store memory due to an internal error: {str(e)}"


async def query_memory(query_text: str) -> str:
    """
    Queries AstraDB asynchronously for memories similar to the query text using vector search.
    (Corrected for astrapy 0.7.4 options syntax)
    """
    if not query_text or not query_text.strip():
        logger.warning("Attempted to query memory with empty text.")
        return "Error: Cannot query memory with empty text."

    logger.info(f"Attempting to query memory with: '{query_text}'")
    collection = await _initialize_astra_db_resources()
    if not collection:
        return "Error: Could not connect to or initialize the memory database."

    loop = asyncio.get_running_loop()
    try:
        # 1. Generate query embedding
        logger.debug("Generating query embedding...")
        query_vector = embedding_generator.get_embedding(query_text)
        if not query_vector or len(query_vector) != EMBEDDING_DIMENSION:
             logger.error(f"Failed to generate valid embedding for query: '{query_text}'")
             return "Error: Failed to process query for memory search (embedding error)."
        logger.debug("Query embedding generated.")

        # 2. Perform vector search in AstraDB asynchronously
        logger.debug(f"Performing vector search in '{COLLECTION_NAME}'...")

        # --- CORRECTED FIND CALL for astrapy 0.7.4 ---
        # Define options dictionary using valid fields for this version
        find_options = {
            "limit": VECTOR_SEARCH_LIMIT,
            "includeSimilarity": True # Explicitly request similarity score
            # "projection" is NOT valid here
        }

        results = await loop.run_in_executor(
            None,
            lambda: collection.find(
                sort={"$vector": query_vector},
                options=find_options # Pass corrected options
            )
        )
        # --- END OF CORRECTION ---


        # Check if results structure is as expected
        if results is None or 'data' not in results or 'documents' not in results['data']:
             logger.error(f"Unexpected response structure from AstraDB find operation: {results}")
             return "Error: Received an unexpected response from the memory database."

        found_docs = results['data']['documents']

        if not found_docs:
            logger.info("No relevant memories found.")
            return "No relevant memories found."
        else:
            logger.info(f"Found {len(found_docs)} relevant memories.")
            response_parts = ["Found relevant memories:"]
            for doc in found_docs:
                # Safely get text and similarity
                # Similarity is likely under '$similarity' key when includeSimilarity is true
                similarity = doc.get('$similarity', 'N/A')
                # The full document is returned, 'text' field should be directly accessible
                text = doc.get('text', 'Error: Text missing from memory document.')
                # Format similarity nicely
                sim_str = f"{similarity:.4f}" if isinstance(similarity, float) else str(similarity)
                response_parts.append(f"- (Similarity: {sim_str}) {text}")

            return "\n".join(response_parts)

    except Exception as e:
        # Catch potential errors during embedding or search
        logger.error(f"Error querying memory from AstraDB: {e}", exc_info=True)
        # Consider checking for specific DB exceptions
        return f"Error: Could not query memory due to an internal error: {str(e)}"