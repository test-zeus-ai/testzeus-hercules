from typing import List, Optional, Dict, Any, cast

import autogen
from autogen import AssistantAgent
from autogen.agentchat.contrib.retrieve_user_proxy_agent import RetrieveUserProxyAgent
from testzeus_hercules.core.memory.static_data_loader import (
    # load_data,
    # list_load_data,
    get_test_data_file_paths,
)
from testzeus_hercules.config import get_global_conf
import os
import tempfile
import uuid
import datetime
import shutil
from testzeus_hercules.utils.logger import logger
from unstructured.partition.auto import partition
from unstructured.documents.elements import Text, Title, NarrativeText


class DynamicLTM:
    _instances: Dict[str, "DynamicLTM"] = {}

    def __new__(
        cls,
        namespace: str = "default",
        singleton: bool = True,
        llm_config: Optional[Dict[str, Any]] = None,
    ) -> "DynamicLTM":
        if singleton:
            # trim namespace to last 60 characters
            namespace = namespace[-60:]
            if namespace not in cls._instances:
                cls._instances[namespace] = super().__new__(cls)
                cls._instances[namespace]._initialize(namespace, llm_config)
            return cls._instances[namespace]
        else:
            instance = super().__new__(cls)
            instance._initialize(namespace, llm_config)
            return instance

    def _initialize(
        self,
        namespace: str,
        llm_config: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Initialize the DynamicLTM instance with RAG capabilities."""
        self.namespace = namespace
        self.static_data_list = get_test_data_file_paths()
        self.llm_config = llm_config or {}

        # Get configuration for vector DB persistence
        config = get_global_conf()
        self.reuse_vector_db = config.should_reuse_vector_db()
        self.vector_db_path = os.path.join(
            tempfile.gettempdir(), f"vector_db_{namespace}"
        )

        # Initialize RAG agents
        self.assistant = AssistantAgent(
            name="rag_assistant",
            system_message="""Role:
I am a memory retrieval agent designed to assist other agents by retrieving information explicitly stored in my memory.

Retrieval Guidelines:
	1.	Data Scope: Provide only information that is explicitly stored in my memory.
	2.	Structured Response: Organize your response clearly and logically.
	3.	Agent Identification: Use the target_helper function to identify which agent is making the request.
	4.	Comprehensive Details: Include all details required for executing the task and performing tests.
	5.	Balanced Sources: Present data from all relevant sources without bias.
	6.	Contextual Completeness: Ensure your response contains complete contextual information.
	7.	Test Data: Return all required test data and test dependency information as specified by the task.
	8.	Code Explanations: If the idea is simple, provide code examples to explain the concept clearly.
    9.  DON'T EVERY GIVE EXAMPLES OR IMAGINARY SCENARIOS, ONLY USE THE INFORMATION YOU HAVE.

Limitations:
I work strictly with data that has been explicitly stored in my memory.""",
            llm_config=self.llm_config,
            human_input_mode="NEVER",
        )

        # # Create a temporary directory for storing test-specific documents
        # docs_dir = os.path.join(tempfile.gettempdir(), f"rag_docs_{namespace}")
        # os.makedirs(docs_dir, exist_ok=True)

        # Handle vector DB persistence based on configuration
        if not self.reuse_vector_db and os.path.exists(self.vector_db_path):
            logger.info(f"Cleaning up old vector DB at {self.vector_db_path}")
            try:
                shutil.rmtree(self.vector_db_path)
            except Exception as e:
                logger.error(f"Error cleaning up vector DB: {str(e)}")

        # Initialize RetrieveUserProxyAgent with the static data
        self.rag_agent = RetrieveUserProxyAgent(
            name="rag_proxy",
            retrieve_config={
                "task": "qa",
                "docs_path": self.static_data_list,
                "chunk_token_size": 10000,
                "model": self.llm_config.get("config_list", [{}])[0].get(
                    "model", "gpt-4o-mini"
                ),
                "collection_name": f"ad{namespace}",  # Use namespace in collection name
                "get_or_create": self.reuse_vector_db,  # Use config to determine get_or_create
                "persist_dir": self.vector_db_path,  # Set persistence directory
            },
            human_input_mode="NEVER",
        )

        # Initialize vector DB with static data
        self._ensure_vector_db_initialized()

    def _ensure_vector_db_initialized(self) -> None:
        """Ensure vector DB is initialized with current content."""
        try:
            if (
                not hasattr(self.rag_agent, "_vector_db")
                or self.rag_agent._vector_db is None
            ):
                # Set these before initialization to ensure proper collection handling
                self.rag_agent._collection = False
                self.rag_agent._get_or_create = self.reuse_vector_db

                # Initialize the vector DB
                self.rag_agent._init_db()

                # After successful initialization
                self.rag_agent._collection = True
            else:
                # If DB exists, ensure we're using the right collection
                collection_name = self.rag_agent._retrieve_config.get(
                    "collection_name", f"ad{self.namespace}"
                )
                if hasattr(self.rag_agent._vector_db, "get_collection"):
                    try:
                        # Get the collection object
                        collection = self.rag_agent._vector_db.get_collection(
                            collection_name
                        )
                        # Set it as the active collection
                        self.rag_agent._vector_db.active_collection = collection
                        logger.info(
                            f"Successfully set active collection: {collection_name}"
                        )
                    except Exception as e:
                        logger.error(f"Error setting active collection: {str(e)}")
                        # Reset flags on error
                        self.rag_agent._collection = False
                        self.rag_agent._get_or_create = self.reuse_vector_db

        except Exception as e:
            import traceback

            traceback.print_exc()
            logger.error(f"Error initializing vector DB: {str(e)}")
            # Reset flags on error
            self.rag_agent._collection = False
            self.rag_agent._get_or_create = self.reuse_vector_db

    def _process_content(self, content: str, is_text: bool = True) -> str:
        """Process content using unstructured.io based on content type."""
        try:
            if is_text:
                # For text content, create a temporary file and process it
                temp_file = os.path.join(
                    tempfile.gettempdir(), f"temp_{uuid.uuid4()}.txt"
                )
                with open(temp_file, "w") as f:
                    f.write(content)

                elements = partition(temp_file)
                os.remove(temp_file)  # Clean up temporary file
            else:
                # For non-text content, process directly using partition
                elements = partition(content)

            # Extract and combine text from relevant element types
            processed_text = []
            for element in elements:
                if isinstance(element, (Text, Title, NarrativeText)):
                    processed_text.append(str(element))

            return "\n".join(processed_text)
        except Exception as e:
            logger.error(f"Error processing content with unstructured.io: {str(e)}")
            return content  # Fallback to original content if processing fails

    def save_content(self, content: str, is_text: bool = True) -> None:
        """
        Save content to memory using vector database with unstructured.io processing.

        Args:
            content (str): Content to be saved to memory.
            is_text (bool): Whether the content is text or another format.
        """
        # if not self.static_data_list:
        #     logger.warning("Memory docs path not initialized")
        #     return

        if not content.strip():
            logger.warning("Empty content provided, skipping save")
            return

        try:
            # Process content using unstructured.io

            processed_content = self._process_content(content, is_text)

            # Generate a unique ID for the document
            doc_id = str(uuid.uuid4())

            # Save to file system for persistence
            # with open(self.static_data_list, "a") as f:
            #     f.write(f"\n--- Document {doc_id} ---\n{processed_content}\n")

            # Ensure vector DB is initialized
            self._ensure_vector_db_initialized()

            # Prepare document for insertion
            doc = {
                "id": doc_id,
                "content": processed_content,
                "metadata": {
                    "timestamp": str(datetime.datetime.now()),
                    "is_text": is_text,
                },
            }

            self.rag_agent._vector_db.insert_docs(
                docs=[doc],
                collection_name=self.rag_agent._retrieve_config.get(
                    "collection_name", f"ad{self.namespace}"
                ),
            )
            logger.info(f"Successfully added document {doc_id} to vector DB")

        except Exception as e:
            logger.error(f"Error saving content to memory: {str(e)}")
            # Still try to save to file even if vector DB fails
            # with open(self.static_data_list, "a") as f:
            #     f.write("\n" + content)

    async def query(self, context: str) -> str:
        """
        Query the memory system using RAG capabilities.

        Args:
            context (str): The context to query memory with.

        Returns:
            str: Retrieved memory content or empty string if no relevant information found.
        """
        problem = (
            "EQUIP me with all relevant INFORMATION, ENVIRONMENT DATA, TEST DATA, TEST DEPENDENCIES TO SOLVE THE TASK: "
            + context
        )
        result = self.rag_agent.initiate_chat(
            self.assistant, message=self.rag_agent.message_generator, problem=problem
        )
        return (
            result.chat_history[-1]["content"]
            if result and hasattr(result, "chat_history")
            else result.summary
        )

    def clear(self) -> None:
        """Clear the memory while preserving static data."""
        if not self.static_data_list:
            logger.warning("Memory docs path not initialized")
            return

        # with open(self.static_data_list, "w") as f:
        #     f.write(self.static_data)

        # Update RAG agent's docs path
        self.rag_agent._retrieve_config["docs_path"] = self.static_data_list

    @property
    def memory_path(self) -> Optional[List[str]]:
        """Get the path to the memory file."""
        return self.static_data_list

    def get_agents(self) -> tuple[AssistantAgent, RetrieveUserProxyAgent]:
        """Get the RAG agents used by this memory system."""
        return self.assistant, self.rag_agent
