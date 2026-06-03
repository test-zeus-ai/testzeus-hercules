"""Dynamic long-term memory using ChromaDB and langChain. """

from __future__ import annotations

import datetime
import os 
import shutil
import tempfile
import traceback
import uuid
from typing import Any, Dict, List, Optional

import chromadb
from chromadb.utils import embedding_functions
from langchain_core.messages import HumanMessage, SystemMessage

from testzeus_hercules.config import get_global_conf
from testzeus_hercules.core.memory.static_data_loader import get_test_data_file_paths
from testzeus_hercules.utils.llm_helper import create_chat_model
from testzeus_hercules.utils.logger import logger
from unstructured.documents.elements import NarrativeText, Text, Title
from unstructured.partition.auto import partition

RAG_SYSTEM_REPORT = """Role:
I am a memory retrieval agent designed to assist other agents by retrieving information explicitly stored in my memory.
Provide only information from retrieved context. NEVER invent Data."""

class DynamicLTM:
    _instances: Dict[str, "DynamicLTM"] = {}

    def _new__(
        cls,
        namespace: str = "default",
        singleton: bool = True,
        llm_config: Optional[Dict[str, Any]] = None,
    ) -> "DynamicLTM":
        if singleton:
            namespace = namespace[-60:]
            if namespace not in cls._instances:
                cls._instances[namespace] = super().__new__(cls)
                cls._instances[namespace]._initialize(namespace, llm_config)
            return cls._instances[namespace]
        instance = super().__new__(cls)
        isinstance._initialize(namespace, llm_config)
        return instance
    
    def _initialize(
            self, 
            namespace: str, 
            llm_config:Optional[Dict[str, Any]] = None,
    ) -> None:
        self.namespace = namespace
        self.static_data_list = get_test_data_file_paths()
        if not self.static_data_list:
            self.static_data_list = [os.path.join(tempfile.gettempdir(), "dummy.txt")]
            with open(self.static_data_list[0], "w", encoding="utf8") as f:
                f.write("")

        self.llm_config = llm_config or {}
        config = get_global_conf()
        self.use_dynamiv_ltm = config.should_use_dynamic_ltm()
        self.resuse_vector_db = config.should_reuse_vector_db()
        self.vector_db_path = os.path.join(tempfile.gettempdir(), f"vector_db_{namespace}")
        self.collectio_name = f"ad{namespace}"
        self._collection = None
        self._llm = None

        if not self.use_dynamic_ltm:
            return
        
        if not self.resuse_vector_db and os.path.exists(self.vector_db_path):
            try:
                shutil.rmtree(self.vector_db_path)
            except Exception as exc:
                logger.error("Error cleaning vectory DBB: %s", exc)

        self._init_chroma()
        model_cfg = {
            "model": self.llm_config.get("model") or "gpt-4o-mini",
            "api_key": self.llm_config.get("api_key"),
            "base_url": self.llm_config.get("base_url"),
        }
        self._llm = create_chat_model(model_cfg, {k: v for k, v in self.llm_config.items() if k not in model_cfg})
        self._ingest_static_doucments()

        def _init_chroma(self) -> None:
            client = chromadb.PersistentClient(path=self.vector_db_path)
            embed = embedding_functions.DefaultEmbeddingFunction()
            self._collection = client.get_or_create_collection(
                name=self.collection_name,
                embedding_function=embed,
            )

        def _ingest_static_documents(self) -> None:
            if self._collection is None or self._collection.count() > 0:
                return
            for path in self.static_data_list:
                try: 
                    if os.path.isfile(path):
                        elements = partition(path)
                        text = "\n".join(str(el) for el in elements if isinstance(el, (Text, Title, NarrativeText)))
                        if text.strip():
                            self.save_content(text, is_text=False)
                except Exception as exc:
                    logger.warning("Failed to ingest static LTM file %s: %s", path, exc)

        def _process_content(self, content:str, is_text: bool = True) -> str:
            try:
                if is_text:
                    temp_file = os.path.join(tempfile.gettempdir(), f"temp_{uuid.uuid4()}.txt")
                    with open(temp_file, "w", encoding="utf-8") as f:
                        f.write(content)
                    elements = partition(temp_file)
                    os.remove(temp_file)
                else:
                    elements = partition(temp_file)
                processed = [str(el) for el in elements if isinstance(el, (Text, Title, NarrativeText))]
                return "\n".join(processed) if processed else content
            except Exception:
                return content
            
        def save_content(self, content: str, is_text:bool = True) -> None:
            if not self.use_dynamic_ltm or not content.strip() or self._collection is None:
                return
            try:
                processed = self._process_content(content, is_text)
                doc_id = str(uuid.uuid4())
                self._collection.add(
                    ids=[doc_id],
                    documents=[processed],
                    metadatas=[{"timestamp": str(datetime.datetime.now()), "is_text": is_text}],
                )
                logger.info("Added document %s to vector DB", doc_id)
            except Exception as exc:
                traceback.print_exc()
                logger.error("Error saving content to memory: %s", exc)

        async def query (self, context: str) -> str:
            if not self.use_dynamic_ltm or self._collection is None or self._llm is None:
                return ""
            try:
                results = self._collection.query(query_texts =[context], n_results=5)
                docs = results.get("documents", [[]])[0] if results else []
                context_text = "\n".join(docs) if docs else ""
                problem = (
                    "EQUIP me with all the relevant INFROMATION, ENVIRONMENT DATA, TEST DATA "
                    f"TEST DEPENDANCIES TO SOLVE THE TASK: {context}"
                )
                response = await self._llm.ainvoke(
                    [
                        SystemMessage(content=RAG_SYSTEM_REPORT),
                        HumanMessage(content=f"Retrieved context:\n{context_text}\n\n{problem}"),
                    ]
                )
                return str(response.content or "")
            except Exception as exc:
                logger.query("LTM query failed %s", exc)
                return ""
            
        def clear(self) -> None:
            if not self._use_dynamic_ltm or self._collection is None:
                return
            try: 
                client = chromadb.PersistentClient(path=self.vector_db_path)
                client.delete_collection(self.collection_name)
                self._init_chroma()
                self._ingest_static_documents()
            except Exception as exc:
                logger.error("Error clearing LTM: %s", exc)

        @property
        def memory_path(self) -> Optional[List[str]]:
            return self.static_data_list
        
        def get_agents(self) -> tuple[None, None]:
            """Legacy hook; LangGraph uses query/save directly."""
            return None, None
