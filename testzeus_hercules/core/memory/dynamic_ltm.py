import datetime
import functools
import io
import os
import shutil
import sys
import tempfile
import uuid
from typing import Any, Dict, List, Optional, cast

import autogen
from autogen import AssistantAgent, UserProxyAgent
from autogen.agentchat.contrib.retrieve_user_proxy_agent import RetrieveUserProxyAgent
from testzeus_hercules.config import get_global_conf
from testzeus_hercules.core.memory.static_data_loader import (  # load_data,; list_load_data,
    get_test_data_file_paths,
)
from testzeus_hercules.utils.logger import logger
from unstructured.documents.elements import NarrativeText, Text, Title
from unstructured.partition.auto import partition


def suppress_prints(func):
    """Decorator to suppress print statements within a function."""

    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        silent_stdout = io.StringIO()
        original_stdout, sys.stdout = sys.stdout, silent_stdout  # Redirect stdout

        try:
            return func(*args, **kwargs)  # Execute function
        finally:
            sys.stdout = original_stdout  # Restore stdout

    return wrapper


class SilentRetrieveUserProxyAgent(RetrieveUserProxyAgent):
    """Derived class to suppress print outputs from noisy methods."""

    @suppress_prints
    def initiate_chat(self, *args: Any, **kwargs: Any) -> Any:
        return super().initiate_chat(*args, **kwargs)

    @suppress_prints
    async def a_initiate_chat(self, *args: Any, **kwargs: Any) -> Any:
        return await super().a_initiate_chat(*args, **kwargs)

    @suppress_prints
    def _init_db(self, *args: Any, **kwargs: Any) -> Any:
        return super()._init_db(*args, **kwargs)

    @suppress_prints
    def _get_context(self, *args: Any, **kwargs: Any) -> Any:
        return super()._get_context(*args, **kwargs)

    @suppress_prints
    def _generate_message(self, *args: Any, **kwargs: Any) -> Any:
        return super()._generate_message(*args, **kwargs)

    @suppress_prints
    def _generate_retrieve_user_reply(self, *args: Any, **kwargs: Any) -> Any:
        return super()._generate_retrieve_user_reply(*args, **kwargs)

    @suppress_prints
    def retrieve_docs(self, *args: Any, **kwargs: Any) -> Any:
        return super().retrieve_docs(*args, **kwargs)


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

        if not self.static_data_list:
            # create a empty dummy file
            self.static_data_list = [os.path.join(tempfile.gettempdir(), "dummy.txt")]
            with open(self.static_data_list[0], "w") as f:
                f.write("")

        self.llm_config = llm_config or {}

        # Get configuration for vector DB persistence
        config = get_global_conf()
        self.use_dynamic_ltm = config.should_use_dynamic_ltm()

        if not self.use_dynamic_ltm:
            # Skip initialization if not using dynamic LTM
            self.assistant = None
            self.rag_agent = None
            return

        logger.info(f"Using dynamic LTM: {self.use_dynamic_ltm}")
        self.reuse_vector_db = config.should_reuse_vector_db()
        self.vector_db_path = os.path.join(
            tempfile.gettempdir(), f"vector_db_{namespace}"
        )

        # Initialize RAG agents only if using dynamic LTM
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
    9.  NEVER GIVE EXAMPLES, SAMPLES OR IMAGINARY SCENARIOS.
    10. ALWAYS RETURN LATEST DATA IF SIMILAR DATA FOUND IN MEMORY.
    11. NEVER CHANGE THE DATA.

Limitations:
I work strictly with data that has been explicitly stored in my memory.""",
            llm_config=self.llm_config,
            human_input_mode="NEVER",
        )

        # Handle vector DB persistence based on configuration
        if not self.reuse_vector_db and os.path.exists(self.vector_db_path):
            logger.info(f"Cleaning up old vector DB at {self.vector_db_path}")
            try:
                shutil.rmtree(self.vector_db_path)
            except Exception as e:
                logger.error(f"Error cleaning up vector DB: {str(e)}")

        # Initialize RetrieveUserProxyAgent with the static data
        self.rag_agent = SilentRetrieveUserProxyAgent(
            name="rag_proxy",
            retrieve_config={
                "task": "qa",
                "docs_path": self.static_data_list,
                "chunk_token_size": 20000,
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
        if not self.use_dynamic_ltm:
            # Skip saving when using static LTM
            return

        if not content.strip():
            logger.warning("Empty content provided, skipping save")
            return

        try:
            processed_content = self._process_content(content, is_text)
            doc_id = str(uuid.uuid4())

            self._ensure_vector_db_initialized()

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

    async def query(self, context: str) -> str:
        """
        Query the memory system using RAG capabilities.

        Args:
            context (str): The context to query memory with.

        Returns:
            str: Retrieved memory content or empty string if no relevant information found.
        """
        if not self.use_dynamic_ltm:
            return ""

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
        if not self.use_dynamic_ltm:
            return

        if self.rag_agent and hasattr(self.rag_agent, "_vector_db"):
            self.rag_agent._vector_db.delete_collection(f"ad{self.namespace}")
            self.rag_agent._retrieve_config["docs_path"] = self.static_data_list

    @property
    def memory_path(self) -> Optional[List[str]]:
        """Get the path to the memory file."""
        return self.static_data_list

    def get_agents(self) -> tuple[AssistantAgent, RetrieveUserProxyAgent]:
        """Get the RAG agents used by this memory system."""
        if not self.use_dynamic_ltm:
            return None, None
        return self.assistant, self.rag_agent
