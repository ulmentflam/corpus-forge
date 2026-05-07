# Sources

## Overview

Sources in corpus-forge are responsible for discovering and parsing data from various origins. Each source plugin implements the `Source` protocol (or extends `WatchedSource` for file-based sources) and knows how to:
- Discover files or data entries to process
- Parse those entries into standardized `RawDocument` or `RawConversation` objects
- Optionally watch for changes in real-time

## Built-in Sources

### Markdown Vault Source (`markdown_vault`)

Ingests text data from Obsidian-style markdown vaults.

**Configuration:**
```toml
[[datasets.sources]]
plugin         = "markdown_vault"
vault_root     = "~/Library/Mobile Documents/iCloud~md~obsidian/Documents"
vault_names    = []  # [] = all subdirectories
exclude_globs  = [".obsidian/**", ".trash/**", ".*"]
chunker        = "markdown"
chunker_config = { max_chars = 1500, overlap = 200 }
```

**Features:**
- Recursive scanning of markdown files
- Automatic exclusion of Obsidian metadata and trash
- Frontmatter parsing for labels and metadata
- Content-based deduplication via SHA256 hashing
- File watching with debounce for real-time updates

### Claude Code Source (`claude_code`)

Ingests chat data from Claude Code session files.

**Configuration:**
```toml
[[datasets.sources]]
plugin            = "claude_code"
projects_root     = "~/.claude/projects"
include_subagents = true
chunker           = "conversation"
chunker_config    = { mode = "per_message", role_prefix = true }
```

**Features:**
- Parses JSONL session files
- Extracts messages, roles, timestamps, and tool usage
- Flattens complex message content (including tool calls and results)
- Preserves conversation structure and threading
- Real-time watching of active sessions

### OpenCode Source (`opencode`)

Ingests chat data from OpenCode storage format.

**Configuration:**
```toml
[[datasets.sources]]
plugin         = "opencode"
storage_root   = "~/.local/share/opencode/storage"
chunker        = "conversation"
chunker_config = { mode = "sliding_window", window_turns = 6, stride_turns = 3 }
```

**Features:**
- Navigates OpenCode's session/message/part directory structure
- Reconstructs messages from parts
- Preserves message relationships and timestamps
- Supports both per-message and sliding window chunking modes
- Handles out-of-order parts and orphaned fragments

## Creating Custom Sources

To create a new source plugin:

1. **Implement the Source protocol** (for non-file-based sources) or **extend WatchedSource** (for file-based sources)

2. **For WatchedSource subclasses:**
   - Override `discover()` to yield paths to process
   - Override `parse(path)` to convert a file into RawDocument or RawConversation
   - The base class handles watching, debouncing, and identity management

3. **For direct Source implementations:**
   - Implement `scan()` to yield RawDocument/RawConversation objects
   - Implement `watch(on_event)` to set up change notifications
   - Implement `identity()` to return a unique identifier for the source

4. **Register your source** in the configuration under the appropriate dataset

**Example - Simple Text File Source:**
```python
from corpus_forge.sources.base import WatchedSource, RawDocument
from pathlib import Path

class SimpleTextSource(WatchedSource):
    name = "simple_text"
    dataset_kind = "text"
    
    def discover(self) -> Iterator[Path]:
        # Yield all .txt files in root directory
        yield from self.root.glob("*.txt")
    
    def parse(self, path: Path) -> RawDocument:
        content = path.read_text(encoding='utf-8')
        return RawDocument(
            source_uri=f"simple://{self.root.name}/{path.name}",
            content_hash=content_hash(content),
            text=content,
            title=path.stem,
            modified_at=path.stat().st_mtime,
            metadata={},
            labels=[]
        )
```

## Data Models

### RawDocument
Represents a single text document:
- `source_uri`: Unique identifier for the source
- `content_hash`: SHA256 of content for deduplication
- `text`: Full text content
- `title`: Optional title (from filename, frontmatter, etc.)
- `modified_at`: Timestamp of last modification
- `metadata`: Arbitrary metadata as key-value pairs
- `labels`: List of (namespace, value) tuples for categorization

### RawConversation
Represents a chat conversation:
- `source_uri`: Unique identifier for the source
- `external_id`: External identifier from the source system
- `content_hash`: SHA256 of all messages for deduplication
- `title`: Optional conversation title
- `started_at`: Timestamp of first message
- `ended_at`: Timestamp of last message
- `messages`: List of RawMessage objects
- `metadata`: Arbitrary metadata as key-value pairs
- `labels`: List of (namespace, value) tuples for categorization

### RawMessage
Represents a single message within a conversation:
- `external_uuid`: External identifier from the source system
- `parent_uuid`: UUID of parent message (for threading)
- `role`: Speaker role ('user', 'assistant', 'system', 'tool')
- `content`: Flattened text content
- `tool_calls`: List of tool invocation details
- `tool_results`: List of tool execution results
- `ts`: Timestamp of message
- `metadata`: Arbitrary metadata as key-value pairs