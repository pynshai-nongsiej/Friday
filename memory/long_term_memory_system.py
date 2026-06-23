#!/usr/bin/env python3
"""
Simple Long-Term Memory System for Claude Code
This system provides persistent storage and retrieval of conversation context
without requiring LLM processing for basic operations.
"""

import json
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Any, Optional


class LongTermMemory:
    def __init__(self, memory_dir: str = "/Users/pynshainongsiej/Mark-XXX/memory"):
        self.memory_dir = Path(memory_dir)
        self.memory_dir.mkdir(exist_ok=True)

        # Memory files
        self.conversation_file = self.memory_dir / "conversation_history.json"
        self.long_term_file = self.memory_dir / "long_term.json"
        self.episodic_file = self.memory_dir / "episodic_memory.json"
        self.semantic_file = self.memory_dir / "semantic_memory.json"

        # Initialize memory files if they don't exist
        self._initialize_memory_files()

    def _initialize_memory_files(self):
        """Initialize memory files with default structure if they don't exist"""
        if not self.conversation_file.exists():
            self._write_json(self.conversation_file, [])

        if not self.long_term_file.exists():
            self._write_json(self.long_term_file, {
                "identity": {},
                "preferences": {},
                "relationships": {},
                "notes": {},
                "relationship_profile": {}
            })

        if not self.episodic_file.exists():
            self._write_json(self.episodic_file, [])

        if not self.semantic_file.exists():
            self._write_json(self.semantic_file, {
                "facts": {},
                "concepts": {},
                "patterns": {}
            })

    def _write_json(self, filepath: Path, data: Any):
        """Write JSON data to file"""
        with open(filepath, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2, ensure_ascii=False)

    def _read_json(self, filepath: Path) -> Any:
        """Read JSON data from file"""
        try:
            with open(filepath, 'r', encoding='utf-8') as f:
                return json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            return None if "history" in str(filepath) else {}

    def add_conversation_entry(self, user_input: str, assistant_response: str):
        """Add a conversation entry to the history"""
        entry = {
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "user": user_input.strip(),
            "assistant": assistant_response.strip()
        }

        # Read existing conversation history
        history = self._read_json(self.conversation_file)
        if history is None:
            history = []

        # Add new entry
        history.append(entry)

        # Keep only last 1000 entries to prevent file from growing too large
        if len(history) > 1000:
            history = history[-1000:]

        # Write back to file
        self._write_json(self.conversation_file, history)

        # Update episodic memory
        self._update_episodic_memory(entry)

        # Update semantic memory (extract facts and patterns)
        self._update_semantic_memory(user_input, assistant_response)

    def _update_episodic_memory(self, entry: Dict[str, str]):
        """Update episodic memory with conversation entry"""
        episodic = self._read_json(self.episodic_file)
        if episodic is None:
            episodic = []

        # Add entry with simplified structure for quick access
        episodic_entry = {
            "timestamp": entry["timestamp"],
            "user_summary": self._summarize_text(entry["user"]),
            "assistant_summary": self._summarize_text(entry["assistant"]),
            "topics": self._extract_topics(entry["user"] + " " + entry["assistant"])
        }

        episodic.append(episodic_entry)

        # Keep last 500 episodic entries
        if len(episodic) > 500:
            episodic = episodic[-500:]

        # Write back to file
        self._write_json(self.episodic_file, episodic)

    def _update_semantic_memory(self, user_input: str, assistant_response: str):
        """Update semantic memory with extracted facts and patterns"""
        semantic = self._read_json(self.semantic_file)
        if semantic is None:
            semantic = {"facts": {}, "concepts": {}, "patterns": {}}

        combined_text = (user_input + " " + assistant_response).lower()

        # Extract potential facts (simple pattern matching)
        # Look for statements like "X is Y", "X has Y", etc.
        fact_patterns = [
            r'(\w+(?:\s+\w+)*)\s+(?:is|are|was|were)\s+(.+)',
            r'(\w+(?:\s+\w+)*)\s+(?:has|have|had)\s+(.+)',
            r'(\w+(?:\s+\w+)*)\s+(?:called|named)\s+(.+)'
        ]

        for pattern in fact_patterns:
            matches = re.findall(pattern, combined_text)
            for subject, fact in matches:
                subject = subject.strip()
                fact = fact.strip()
                if len(subject) > 2 and len(fact) > 2:
                    if subject not in semantic["facts"]:
                        semantic["facts"][subject] = []
                    if fact not in semantic["facts"][subject]:
                        semantic["facts"][subject].append(fact)

        # Extract concepts (noun phrases that appear frequently)
        # Simple approach: extract capitalized phrases and repeated words
        words = re.findall(r'\b[A-Z][a-z]+(?:\s+[A-Z][a-z]+)*\b', user_input + " " + assistant_response)
        for word in words:
            if len(word) > 2:
                if word not in semantic["concepts"]:
                    semantic["concepts"][word] = 0
                semantic["concepts"][word] += 1

        # Keep only concepts that appear at least 2 times
        semantic["concepts"] = {k: v for k, v in semantic["concepts"].items() if v >= 2}

        # Extract patterns (repeated phrases or structures)
        # Look for repeated sequences of words
        word_list = re.findall(r'\b\w+\b', combined_text)
        if len(word_list) > 5:
            # Look for 3-word sequences that repeat
            sequences = {}
            for i in range(len(word_list) - 2):
                seq = " ".join(word_list[i:i+3])
                if len(seq) > 5:  # Ignore very short sequences
                    if seq not in sequences:
                        sequences[seq] = 0
                    sequences[seq] += 1

            # Keep sequences that appear at least 2 times
            repeated_sequences = {k: v for k, v in sequences.items() if v >= 2}
            if repeated_sequences:
                if "sequences" not in semantic["patterns"]:
                    semantic["patterns"]["sequences"] = {}
                semantic["patterns"]["sequences"].update(repeated_sequences)

        # Write back to file
        self._write_json(self.semantic_file, semantic)

    def _summarize_text(self, text: str, max_length: int = 50) -> str:
        """Create a simple summary of text"""
        if len(text) <= max_length:
            return text
        return text[:max_length] + "..."

    def _extract_topics(self, text: str) -> List[str]:
        """Extract potential topics from text"""
        # Simple topic extraction based on nouns and noun phrases
        # Remove common words and extract meaningful terms
        common_words = {
            'the', 'a', 'an', 'and', 'or', 'but', 'in', 'on', 'at', 'to', 'for',
            'of', 'with', 'by', 'is', 'are', 'was', 'were', 'be', 'been', 'being',
            'have', 'has', 'had', 'do', 'does', 'did', 'will', 'would', 'could',
            'should', 'may', 'might', 'must', 'can', 'this', 'that', 'these',
            'those', 'i', 'you', 'he', 'she', 'it', 'we', 'they', 'me', 'him',
            'her', 'us', 'them', 'my', 'your', 'his', 'her', 'its', 'our', 'their'
        }

        # Extract words that are not common words and have reasonable length
        words = re.findall(r'\b[a-zA-Z]{3,}\b', text.lower())
        topics = [word for word in words if word not in common_words]

        # Return unique topics, limited to top 10
        unique_topics = list(dict.fromkeys(topics))  # Preserves order while removing duplicates
        return unique_topics[:10]

    def get_recent_conversations(self, limit: int = 10) -> List[Dict[str, str]]:
        """Get recent conversation entries"""
        history = self._read_json(self.conversation_file)
        if history is None:
            return []
        return history[-limit:] if history else []

    def search_conversations(self, query: str, limit: int = 10) -> List[Dict[str, str]]:
        """Search conversation history for entries containing query"""
        history = self._read_json(self.conversation_file)
        if history is None:
            return []

        query_lower = query.lower()
        matches = []
        for entry in history:
            if (query_lower in entry["user"].lower() or
                query_lower in entry["assistant"].lower()):
                matches.append(entry)
                if len(matches) >= limit:
                    break
        return matches

    def get_episodic_memory(self, limit: int = 10) -> List[Dict[str, Any]]:
        """Get recent episodic memories"""
        episodic = self._read_json(self.episodic_file)
        if episodic is None:
            return []
        return episodic[-limit:] if episodic else []

    def get_semantic_memory(self) -> Dict[str, Any]:
        """Get semantic memory (facts, concepts, patterns)"""
        return self._read_json(self.semantic_file) or {"facts": {}, "concepts": {}, "patterns": {}}

    def get_long_term_memory(self) -> Dict[str, Any]:
        """Get long-term memory (identity, preferences, etc.)"""
        return self._read_json(self.long_term_file) or {
            "identity": {},
            "preferences": {},
            "relationships": {},
            "notes": {},
            "relationship_profile": {}
        }

    def update_long_term_memory(self, category: str, key: str, value: Any):
        """Update long-term memory"""
        lt_memory = self.get_long_term_memory()
        if category not in lt_memory:
            lt_memory[category] = {}
        lt_memory[category][key] = value
        self._write_json(self.long_term_file, lt_memory)

    def clear_memory(self, memory_type: str = "all"):
        """Clear specific memory types"""
        if memory_type in ["all", "conversation"]:
            self._write_json(self.conversation_file, [])
        if memory_type in ["all", "episodic"]:
            self._write_json(self.episodic_file, [])
        if memory_type in ["all", "semantic"]:
            self._write_json(self.semantic_file, {"facts": {}, "concepts": {}, "patterns": {}})
        if memory_type in ["all", "long_term"]:
            self._write_json(self.long_term_file, {
                "identity": {},
                "preferences": {},
                "relationships": {},
                "notes": {},
                "relationship_profile": {}
            })


# Example usage
if __name__ == "__main__":
    # Initialize memory system
    memory = LongTermMemory()

    # Add a conversation entry
    memory.add_conversation_entry(
        "Hello, how are you today?",
        "I'm doing well, thank you! How can I assist you?"
    )

    # Retrieve recent conversations
    recent = memory.get_recent_conversations(5)
    print("Recent conversations:", json.dumps(recent, indent=2))

    # Search conversations
    search_results = memory.search_conversations("hello")
    print("Search results:", json.dumps(search_results, indent=2))

    # Get semantic memory
    semantic = memory.get_semantic_memory()
    print("Semantic memory:", json.dumps(semantic, indent=2))