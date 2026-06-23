        if not self.nn_file.exists():
            # Initialize with empty weights - will be built when we have vocabulary
            self._write_json(self.nn_file, {
                "embedding_weights": [],
                "hidden_weights": [],
                "hidden_bias": [],
                "output_weights": [],
                "output_bias": [],
                "vocab_size": 0,
                "embedding_dim": self.embedding_dim,
                "hidden_dim": self.hidden_dim
            })

    def _load_or_initialize_nn(self):
        """Load neural network weights or initialize new ones"""
        # Load vocabulary
        vocab_data = self._read_json(self.vocab_file)
        if vocab_data:
            self.word_to_index = vocab_data.get("word_to_index", {})
            index_to_word_list = vocab_data.get("index_to_word", [])
            self.index_to_word = {i: word for i, word in enumerate(index_to_word_list)}
            self.vocab_size = len(self.word_to_index)

        # Load neural network weights
        nn_data = self._read_json(self.nn_file)
        if nn_data and nn_data.get("vocab_size", 0) > 0:
            # Use loaded weights
            self.embedding_weights = nn_data.get("embedding_weights", [])
            self.hidden_weights = nn_data.get("hidden_weights", [])
            self.hidden_bias = nn_data.get("hidden_bias", [])
            self.output_weights = nn_data.get("output_weights", [])
            self.output_bias = nn_data.get("output_bias", [])
            self.embedding_dim = nn_data.get("embedding_dim", self.embedding_dim)
            self.hidden_dim = nn_data.get("hidden_dim", self.hidden_dim)

            # Initialize neural network with loaded weights
            if self.vocab_size > 0:
                self.neural_network = SimpleNeuralNetwork(
                    self.vocab_size,
                    self.embedding_dim,
                    self.hidden_dim
                )
                self.neural_network.embedding_weights = self.embedding_weights
                self.neural_network.hidden_weights = self.hidden_weights
                self.neural_network.hidden_bias = self.hidden_bias
                self.neural_network.output_weights = self.output_weights
                self.neural_network.output_bias = self.output_bias
        else:
            # Initialize new neural network (will be built when we have vocab)
            self.neural_network = None

    def _update_vocabulary(self, text: str):
        """Update vocabulary with words from text"""
        # Extract words (simple tokenization)
        words = re.findall(r'\b[a-zA-Z]+\b', text.lower())

        # Add new words to vocabulary
        new_words = []
        for word in words:
            if word not in self.word_to_index:
                new_words.append(word)
                self.word_to_index[word] = len(self.word_to_index)
                self.index_to_word.append(word)

        # If we added new words, rebuild neural network
        if new_words:
            self.vocab_size = len(self.word_to_index)
            self._save_vocabulary()
            self._initialize_neural_network()

    def _save_vocabulary(self):
        """Save vocabulary to file"""
        vocab_data = {
            "word_to_index": self.word_to_index,
            "index_to_word": self.index_to_word
        }
        self._write_json(self.vocab_file, vocab_data)

    def _initialize_neural_network(self):
        """Initialize or reinitialize neural network with current vocab size"""
        if self.vocab_size > 0:
            self.neural_network = SimpleNeuralNetwork(
                self.vocab_size,
                self.embedding_dim,
                self.hidden_dim
            )

            # Try to load existing weights if they match vocab size
            nn_data = self._read_json(self.nn_file)
            if nn_data and nn_data.get("vocab_size", 0) == self.vocab_size:
                self.neural_network.embedding_weights = nn_data.get("embedding_weights", [])
                self.neural_network.hidden_weights = nn_data.get("hidden_weights", [])
                self.neural_network.hidden_bias = nn_data.get("hidden_bias", [])
                self.neural_network.output_weights = nn_data.get("output_weights", [])
                self.neural_network.output_bias = nn_data.get("output_bias", [])
            else:
                # Initialize with random weights
                self.neural_network.embedding_weights = [[random.uniform(-0.1, 0.1) for _ in range(self.embedding_dim)]
                                                       for _ in range(self.vocab_size)]
                self.neural_network.hidden_weights = [[random.uniform(-0.1, 0.1) for _ in range(self.hidden_dim)]
                                                    for _ in range(self.embedding_dim)]
                self.neural_network.hidden_bias = [0.0] * self.hidden_dim
                self.neural_network.output_weights = [[random.uniform(-0.1, 0.1) for _ in range(self.vocab_size)]
                                                    for _ in range(self.hidden_dim)]
                self.neural_network.output_bias = [0.0] * self.vocab_size

            # Save the initialized network
            self._save_neural_network()

    def _save_neural_network(self):
        """Save neural network weights to file"""
        if self.neural_network:
            nn_data = {
                "embedding_weights": self.neural_network.embedding_weights,
                "hidden_weights": self.neural_network.hidden_weights,
                "hidden_bias": self.neural_network.hidden_bias,
                "output_weights": self.neural_network.output_weights,
                "output_bias": self.neural_network.output_bias,
                "vocab_size": self.vocab_size,
                "embedding_dim": self.embedding_dim,
                "hidden_dim": self.hidden_dim
            }
            self._write_json(self.nn_file, nn_data)

    def add_conversation_entry(self, user_input: str, assistant_response: str):
        """Add a conversation entry to the history and train neural network"""
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

        # Update vocabulary and train neural network
        combined_text = user_input + " " + assistant_response
        self._update_vocabulary(combined_text)
        self._train_neural_network_on_text(combined_text)

    def _train_neural_network_on_text(self, text: str):
        """Train neural network on a piece of text"""
        if self.neural_network is None or self.vocab_size == 0:
            return

        # Tokenize text
        words = re.findall(r'\b[a-zA-Z]+\b', text.lower())
        if len(words) < 2:
            return

        # Create training pairs (context -> target)
        # Using skip-gram approach: for each word, predict surrounding words
        context_size = 2  # Number of words on each side to consider as context

        for i, target_word in enumerate(words):
            if target_word not in self.word_to_index:
                continue

            target_idx = self.word_to_index[target_word]

            # Get context words
            context_words = []
            start = max(0, i - context_size)
            end = min(len(words), i + context_size + 1)

            for j in range(start, end):
                if j != i and words[j] in self.word_to_index:
                    context_words.append(self.word_to_index[words[j]])

            if context_words:
                # Train on this context-target pair
                self.neural_network.train_batch(context_words, target_idx)

        # Save updated neural network weights periodically
        # (In a real system, we might save less frequently to reduce I/O)
        self._save_neural_network()

    # Keep all the previous methods (_update_episodic_memory, _update_semantic_memory, etc.)
    # but I'll need to include them here for completeness

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

    # Neural network enhanced retrieval methods
    def get_word_embedding(self, word: str) -> Optional[List[float]]:
        """Get embedding vector for a word"""
        if self.neural_network is None or word not in self.word_to_index:
            return None
        word_idx = self.word_to_index[word]
        return self.neural_network.get_embedding(word_idx)

    def find_similar_words(self, word: str, top_n: int = 5) -> List[Tuple[str, float]]:
        """Find words similar to the given word using neural network embeddings"""
        if self.neural_network is None or word not in self.word_to_index:
            return []

        target_embedding = self.get_word_embedding(word)
        if target_embedding is None:
            return []

        similarities = []
        for vocab_word, vocab_idx in self.word_to_index.items():
            if vocab_word == word:
                continue
            vocab_embedding = self.get_word_embedding(vocab_word)
            if vocab_embedding is not None:
                similarity = self.neural_network.cosine_similarity(target_embedding, vocab_embedding)
                similarities.append((vocab_word, similarity))

        # Sort by similarity (descending) and return top_n
        similarities.sort(key=lambda x: x[1], reverse=True)
        return similarities[:top_n]

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

    def get_neural_network_info(self) -> Dict[str, Any]:
        """Get information about the neural network"""
        if self.neural_network is None:
            return {"status": "not_initialized"}

        return {
            "status": "initialized",
            "vocab_size": self.vocab_size,
            "embedding_dim": self.embedding_dim,
            "hidden_dim": self.hidden_dim,
            "vocabulary_sample": list(self.word_to_index.keys())[:10] if self.word_to_index else []
        }

    # Keep the retrieval methods from before
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
        if memory_type in ["all", "vocabulary"]:
            self.word_to_index = {}
            self.index_to_word = []
            self.vocab_size = 0
            self._save_vocabulary()
            self._initialize_neural_network()
        if memory_type in ["all", "neural_network"]:
            self.neural_network = None
            self._save_neural_network()


# Example usage and testing
if __name__ == "__main__":
    # Initialize enhanced memory system
    memory = EnhancedLongTermMemory()

    # Add some conversation entries
    memory.add_conversation_entry(
        "Hello, how are you today?",
        "I'm doing well, thank you! How can I assist you?"
    )

    memory.add_conversation_entry(
        "Can you explain what escrow services are?",
        "Escrow services are financial arrangements where a third party holds and regulates payment of funds required for two parties involved in a given transaction."
    )

    memory.add_conversation_entry(
        "What about cryptocurrency escrow?",
        "For cryptocurrency transactions, you would need specialized crypto escrow services that handle digital assets."
    )

    # Retrieve and display information
    print("=== Recent Conversations ===")
    recent = memory.get_recent_conversations(3)
    print(json.dumps(recent, indent=2))

    print("\n=== Semantic Memory ===")
    semantic = memory.get_semantic_memory()
    print(json.dumps(semantic, indent=2))

    print("\n=== Neural Network Info ===")
    nn_info = memory.get_neural_network_info()
    print(json.dumps(nn_info, indent=2))

    print("\n=== Similar Words to 'escrow' ===")
    similar = memory.find_similar_words("escrow", 3)
    print(similar)

    print("\n=== Similar Words to 'crypto' ===")
    similar = memory.find_similar_words("crypto", 3)
    print(similar)