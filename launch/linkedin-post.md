# LinkedIn post — 600 chars

I kept fixing the same five LangChain / Transformers / OpenAI-SDK bugs across four of my own agent repos. The third time I copy-pasted the same patch, I wrote a fingerprint primitive. Then I wondered if it would generalize.

Darwin is the result. A AST-level structural patching layer for Python agents — LibCST AST gate, fingerprint cache, vendor-neutral LLM fallback, bounded blast radius.

Public benchmark: 17 of 18 real bugs from HuggingFace, LangChain, PyTorch, StackOverflow healed. 100% on a 50-bug strict corpus. $1.09 total LLM spend on the 171-bug run.

Solo builder beta. MIT. Looking for EU design partners. → github.com/Miles0sage/darwin

#Python #AgentReliability #OpenSource
