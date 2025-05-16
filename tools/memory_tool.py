import os
from typing import Dict, Any, List

import asyncio
from sentence_transformers import SentenceTransformer
from dotenv import load_dotenv
from datetime import datetime, timezone

# AstraPy imports for modern usage
from astrapy import DataAPIClient
from astrapy.database import Database
from astrapy.collection import Collection
from astrapy.info import CollectionDefinition
from astrapy.constants import VectorMetric

# Load environment variables
load_dotenv()

# Astra DB Configuration
ASTRA_DB_API_ENDPOINT = os.getenv("ASTRA_DB_API_ENDPOINT")
ASTRA_DB_APPLICATION_TOKEN = os.getenv("ASTRA_DB_APPLICATION_TOKEN")
ASTRA_DB_KEYSPACE = os.getenv("ASTRA_DB_KEYSPACE", "default_keyspace")
MEMORY_COLLECTION_NAME = os.getenv("MEMORY_COLLECTION_NAME", "ai_memory_tool_collection")

# Embedding Model
model_name = "BAAI/bge-small-en-v1.5"
embedding_model = SentenceTransformer(model_name)
embedding_dimension = embedding_model.get_sentence_embedding_dimension()

# Global AstraDB client and collection (initialized on first use)
_db: Database | None = None
_memory_collection: Collection | None = None


def _initialize_astra_db_resources() -> bool:
    """Initializes Astra DB client and memory collection if not already done."""
    global _db, _memory_collection
    if _memory_collection:
        return True

    if not ASTRA_DB_API_ENDPOINT or not ASTRA_DB_APPLICATION_TOKEN:
        print("CRITICAL ERROR: ASTRA_DB_API_ENDPOINT or ASTRA_DB_APPLICATION_TOKEN not set in .env")
        return False

    try:
        client = DataAPIClient()
        _db = client.get_database(
            ASTRA_DB_API_ENDPOINT,
            token=ASTRA_DB_APPLICATION_TOKEN
        )

        _memory_collection = _db.create_collection(
            MEMORY_COLLECTION_NAME,
            definition=CollectionDefinition.builder()
            .set_vector_dimension(embedding_dimension)
            .set_vector_metric(VectorMetric.COSINE)
            .build()
        )
        if _memory_collection is None:
            _memory_collection = _db.get_collection(MEMORY_COLLECTION_NAME)

        if _memory_collection:
            print(
                f"Successfully connected to/created collection '{MEMORY_COLLECTION_NAME}' with dimension {embedding_dimension}.")
            return True
        else:
            print(
                f"CRITICAL ERROR: Failed to get a handle to collection '{MEMORY_COLLECTION_NAME}' after attempting creation/access.")
            return False

    except Exception as e:
        print(f"CRITICAL ERROR during Astra DB Initialization: {type(e).__name__} - {str(e)}")
        # No need for traceback in the final lean version for tool use,
        # but useful for direct script execution if needed.
        # import traceback
        # print("--- Full Traceback ---"); traceback.print_exc(); print("--- End Traceback ---")
        _db = None
        _memory_collection = None
        return False


async def _store_memory_content(text_content: str) -> str:
    """Stores the given text content and its embedding into Astra DB."""
    if not _memory_collection:
        return "Error: Memory database (collection) is not available for storing."

    try:
        embedding = embedding_model.encode(text_content).tolist()
        document_to_insert = {
            "text": text_content,
            "$vector": embedding,
            "created_at": datetime.now(timezone.utc)
        }
        insert_result = _memory_collection.insert_one(document_to_insert)
        if insert_result and insert_result.inserted_id:
            return f"Memory stored successfully. ID: {insert_result.inserted_id}"
        else:
            return "Error: Failed to store memory or no ID returned."
    except Exception as e:
        return f"Error storing memory: {str(e)}"


async def _query_memory_content(query_text: str, top_k: int = 1, similarity_threshold: float = 0.78) -> str:
    """Queries Astra DB for memories similar to the query_text using vector search."""
    if not _memory_collection:
        return "Error: Memory database (collection) is not available for querying."

    try:
        query_prefix = "Represent this sentence for searching relevant passages: "
        text_to_embed_for_query = f"{query_prefix}{query_text}"
        query_embedding = embedding_model.encode(text_to_embed_for_query).tolist()

        search_results: List[Dict[str, Any]] = list(_memory_collection.find(
            sort={"$vector": query_embedding},
            limit=top_k,
            projection={"text": 1},
            include_similarity=True
        ))

        if not search_results:
            return "No memories found matching your query criteria."

        filtered_results = [
            doc for doc in search_results if doc.get('$similarity', 0.0) >= similarity_threshold
        ]

        if not filtered_results:
            return f"No memories found above the similarity threshold of {similarity_threshold:.2f}."

        # For a lean tool, returning just the top result's text might be preferred
        # If multiple results are desired, the AI can request a higher top_k
        # and parse the structured response if needed.
        # Here, we'll format the top result if it exists.

        # Assuming top_k=1 is often desired for direct answers from memory.
        # The AI can specify a higher top_k if it wants more context.
        doc = filtered_results[0]  # Get the top document after filtering
        similarity_score_val = doc.get('$similarity', 0.0)
        text_val = doc.get('text', 'N/A')
        return f"Found: \"{text_val}\" (Similarity: {similarity_score_val:.4f})"

    except Exception as e:
        return f"Error querying memory: {str(e)}"


async def manage_memory_tool(args: Dict[str, Any]) -> str:
    """
    Manages memory operations (store, query) in Astra DB.
    Args:
        command_string (str): 'action:store; content:<text_to_store>' or
                              'action:query; query_text:<text_to_query>; top_k:<num (opt, def 1)>; threshold:<float (opt, def 0.78)>'
    """
    if not _memory_collection:
        if not _initialize_astra_db_resources():
            return "Error: Memory tool failed to connect/initialize Astra DB. Check env vars and DB setup."

    command_string = args.get("command_string")
    if not command_string or not isinstance(command_string, str):
        return "Error: Missing 'command_string'. Use 'action:store; content:...' or 'action:query; query_text:...'."

    params: Dict[str, str] = {}
    try:
        for part in command_string.split(';'):
            if ':' in part:
                key, value = part.split(':', 1)
                params[key.strip().lower()] = value.strip()
    except Exception:
        return "Error: Invalid format for command_string. Use 'key:value;' pairs."

    action = params.get("action")

    if action == "store":
        content_to_store = params.get("content")
        if not content_to_store:
            return "Error: 'content' is missing for store action."
        return await _store_memory_content(content_to_store)

    elif action == "query":
        query_text = params.get("query_text")
        if not query_text:
            return "Error: 'query_text' is missing for query action."

        top_k_str = params.get("top_k", "1")  # Default top_k to 1 for lean query
        try:
            top_k = int(top_k_str)
            top_k = max(1, min(top_k, 5))  # Clamp top_k for tool use
        except ValueError:
            top_k = 1

        threshold_str = params.get("threshold", "0.78")
        try:
            similarity_threshold = float(threshold_str)
            similarity_threshold = max(0.0, min(similarity_threshold, 1.0))
        except ValueError:
            similarity_threshold = 0.78  # Keep BGE default

        return await _query_memory_content(query_text, top_k=top_k, similarity_threshold=similarity_threshold)

    else:
        return f"Error: Unknown action '{action}'. Supported: 'store', 'query'."


# # --- Minimal Test Block ---
# async def _run_minimal_test():
#     print("--- Starting Minimal Memory Tool Test (Model: BAAI/bge-small-en-v1.5) ---")
#
#     # Phase 0: Ensure collection is clean (optional, but good for repeatable tests)
#     # This assumes MEMORY_COLLECTION_NAME is set.
#     if not MEMORY_COLLECTION_NAME:
#         print("ERROR: MEMORY_COLLECTION_NAME environment variable not set. Cannot run tests.")
#         return
#
#     print(f"\n[Phase 0: Attempting to clean collection '{MEMORY_COLLECTION_NAME}']")
#     if not _db:  # Initialize _db if it's not already (e.g. first run)
#         if not _initialize_astra_db_resources():  # This will print its own errors if it fails
#             print("Halting test due to DB initialization failure in pre-test cleanup.")
#             return
#         # If _initialize_astra_db_resources succeeded but _db is somehow still None (should not happen)
#         if not _db:
#             print("ERROR: _db object is None after initialization attempt. Cannot clean collection.")
#             return  # Can't proceed
#
#     try:
#         _db.drop_collection(MEMORY_COLLECTION_NAME)
#         print(f"Collection '{MEMORY_COLLECTION_NAME}' dropped successfully or did not exist.")
#         global _memory_collection  # Reset to ensure it's recreated
#         _memory_collection = None
#     except Exception as e:
#         print(f"Note: Could not drop collection '{MEMORY_COLLECTION_NAME}' (may not exist or other issue): {e}")
#
#     # 1. Connection Check (Implicitly done by initialization)
#     print("\n[Test 1: Connection and Collection Initialization]")
#     if not _initialize_astra_db_resources():
#         print("Test FAILED: Could not initialize Astra DB. Check .env and Astra DB status.")
#         return
#     print("Test PASSED: Astra DB Resources Initialized Successfully.")
#
#     # 2. Add Data
#     print("\n[Test 2: Storing Data]")
#     test_content = "The Solar System consists of the Sun and the objects that orbit it."
#     store_args = {"command_string": f"action:store; content:{test_content}"}
#     store_result = await manage_memory_tool(store_args)
#     print(f"Store Result: {store_result}")
#     if "Error:" in store_result:
#         print("Test FAILED: Store operation encountered an error.")
#         return
#     print("Test PASSED: Data stored.")
#
#     await asyncio.sleep(2)  # Brief pause for indexing
#
#     # 3. Query Data
#     print("\n[Test 3: Querying Stored Data]")
#     query_text = "What is the Solar System?"
#     # Using default top_k=1 and threshold=0.78 from manage_memory_tool
#     query_args = {"command_string": f"action:query; query_text:{query_text}"}
#     query_result = await manage_memory_tool(query_args)
#     print(f"Query Result for '{query_text}':\n{query_result}")
#     if "Error:" in query_result:
#         print("Test FAILED: Query operation encountered an error.")
#     elif "No memories found" in query_result:
#         print("Test FAILED: Query did not find the stored relevant content above threshold.")
#     elif test_content not in query_result:
#         print("Test FAILED: Query result did not contain the exact stored text (similarity might be an issue).")
#     else:
#         print("Test PASSED: Query retrieved relevant data.")
#
#     print("\n--- Minimal Memory Tool Test Completed ---")
#
#
# if __name__ == '__main__':
#     if not (ASTRA_DB_API_ENDPOINT and ASTRA_DB_APPLICATION_TOKEN):
#         print("ERROR: ASTRA_DB_API_ENDPOINT and/or ASTRA_DB_APPLICATION_TOKEN not found in environment.")
#         print("Make sure your .env file is correctly set up.")
#         exit(1)
#     if not MEMORY_COLLECTION_NAME:
#         print("ERROR: MEMORY_COLLECTION_NAME environment variable not set. Please set it in your .env file.")
#         print("Example: MEMORY_COLLECTION_NAME=ai_memory_tool_collection")
#         exit(1)
#
#     asyncio.run(_run_minimal_test())