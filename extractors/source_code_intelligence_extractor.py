"""
[ SOURCE CODE INTELLIGENCE KERNEL ]
Structural analysis engine for programming languages and configuration scripts.

PIPELINE:
1. Lexical Analysis (Pygments): Identifies the programming language and 
   tokenizes the source to isolate logic from commentary.
2. Structural Extraction: Surgically extracts Class names, Function/Method 
   definitions, and dependency imports.
3. Annotation Harvesting: Scans for 'TODO', 'FIXME', and 'BUG' markers to 
   provide actionable development context.
4. Documentation Capture: Extracts top-level docstrings and block comments 
   to populate the search index with high-level summaries.

REQUIRES: Pygments.
"""

MANIFEST = {
    "id": "com.docvault.code.structural",
    "version": "1.0.0",
    "name": "Source Code Intelligence",
    "extensions": ["py", "js", "ts", "java", "cpp", "c", "go", "rs", "rb", "php", "sh", "css", "html", "yaml", "toml"],
    "requires": ["pygments"]
}

__description__ = (
    "A structural code analysis engine. It parses source files to identify "
    "classes, functions, imports, and developer annotations (TODOs), "
    "transforming raw code into a structured, searchable summary."
)

import os
import re
from pygments import lexers
from pygments import token as tokens
from core import logger
from core.extractors.base import ExtractorContext

def extract(file_path: str, ctx: ExtractorContext) -> tuple:
    """
    Perform lexical and structural analysis on a source code file.
    """
    logger.info(f"Analyzing Code: {os.path.basename(file_path)}", ext="code-ai")
    meta = {"language": "unknown"}
    parts = []

    try:
        # 1. Load and detect language
        with open(file_path, 'r', encoding='utf-8', errors='replace') as f:
            code = f.read()

        try:
            lexer = lexers.get_lexer_for_filename(file_path)
            meta["language"] = lexer.name
        except:
            lexer = lexers.guess_lexer(code)
            meta["language"] = lexer.name

        parts.append(f"[ LANGUAGE: {meta['language']} ]")

        # 2. Token Analysis
        defs = {"classes": [], "functions": [], "imports": []}
        annotations = []
        doc_buffer = []
        
        # Simplified token walking
        for ttype, value in lexer.get_tokens(code):
            val = value.strip()
            if not val: continue

            # Definitions
            if ttype in tokens.Name.Class: defs["classes"].append(val)
            elif ttype in tokens.Name.Function: defs["functions"].append(val)
            elif ttype in tokens.Name.Namespace: defs["imports"].append(val)
            
            # Comments & Annotations
            elif ttype in tokens.Comment or ttype in tokens.Comment.Multiline:
                if any(x in val.upper() for x in ["TODO", "FIXME", "BUG", "HACK"]):
                    annotations.append(val)
                if len(doc_buffer) < 5 and len(val) > 20: # Capture early descriptive comments
                    doc_buffer.append(val)

        # 3. Build Report
        if defs["classes"]:
            parts.append("[ CLASSES ]\n" + ", ".join(sorted(list(set(defs["classes"])))))
        
        if defs["functions"]:
            # Limit to top 20 to avoid index bloating
            fn_list = sorted(list(set(defs["functions"])))[:20]
            parts.append("[ FUNCTIONS ]\n" + ", ".join(fn_list))

        if annotations:
            parts.append("[ ANNOTATIONS ]\n" + "\n".join(annotations[:10]))

        if doc_buffer:
            parts.append("[ SUMMARY / DOCS ]\n" + "\n".join(doc_buffer))

        # Include raw code snippet (first 2KB) for the index
        parts.append("[ SOURCE PREVIEW ]\n" + code[:2000])

        final_text = "\n\n".join(parts)
        meta["structural_success"] = True
        return final_text, None, meta

    except Exception as e:
        return None, f"Code analysis failed: {e}", meta
